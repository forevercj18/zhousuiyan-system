from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import (
    Order, Part, PurchaseOrder, PurchaseOrderItem, SKU, Transfer,
    TransferAllocation, OrderItem, AuditLog, FinanceTransaction, RiskEvent, ApprovalTask, SystemSettings,
    DataConsistencyCheckRun, TransferRecommendationLog,
)


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

    def test_order_finance_transactions_api_should_return_records(self):
        order = Order.objects.get(id=1001)
        FinanceTransaction.objects.create(
            order=order,
            transaction_type='deposit_received',
            amount=Decimal('60.00'),
            notes='API测试押金',
            created_by=self.user,
        )
        resp = self.client.get(reverse('api_order_finance_transactions', kwargs={'order_id': order.id}))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(len(payload['data']), 1)
        self.assertEqual(payload['data'][0]['transaction_type'], 'deposit_received')
        self.assertEqual(payload['data'][0]['amount'], '60.00')

    def test_finance_reconciliation_api_should_return_mismatch_with_suggestions(self):
        order = Order.objects.get(id=1001)
        OrderItem.objects.create(
            order=order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=self.sku.rental_price,
        )
        FinanceTransaction.objects.create(
            order=order,
            transaction_type='deposit_received',
            amount=Decimal('10.00'),
            created_by=self.user,
        )
        resp = self.client.get(reverse('api_finance_reconciliation') + '?mismatch_only=1')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['meta']['total'], 1)
        self.assertTrue(payload['data'][0]['has_mismatch'])
        self.assertIn('押金多收', ''.join(payload['data'][0]['suggestions']))
        resp2 = self.client.get(reverse('api_finance_reconciliation') + '?mismatch_only=1&mismatch_field=deposit&min_diff_amount=5')
        self.assertEqual(resp2.status_code, 200)
        payload2 = resp2.json()
        self.assertTrue(payload2['success'])
        self.assertEqual(payload2['meta']['mismatch_field'], 'deposit')
        self.assertEqual(payload2['meta']['min_diff_amount'], '5')
        self.assertEqual(payload2['meta']['total'], 1)
        self.assertIn('mismatch_stats', payload2['meta'])
        self.assertGreaterEqual(int(payload2['meta']['mismatch_stats']['abnormal']), 1)

    def test_dashboard_stats_api_returns_real_counts(self):
        resp = self.client.get(reverse('api_dashboard_stats'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['data']['total_orders'], 1)
        self.assertEqual(payload['data']['total_skus'], 1)
        self.assertEqual(payload['data']['low_stock_parts'], 1)
        self.assertIn('completed_orders', payload['data'])
        self.assertIn('due_within_7_days_count', payload['data'])
        self.assertIn('fulfillment_rate', payload['data'])
        self.assertIn('cancel_rate', payload['data'])
        self.assertIn('avg_transit_days', payload['data'])

    def test_dashboard_role_view_should_return_role_specific_payload(self):
        resp = self.client.get(reverse('api_dashboard_role_view'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['data']['role'], 'admin')
        self.assertEqual(payload['data']['view_type'], 'business')
        self.assertTrue(any(card['key'] == 'total_revenue' for card in payload['data']['focus_cards']))
        self.assertTrue(any(card['key'] == 'transfer_available_count' and card.get('url_name') == 'transfers_list' for card in payload['data']['focus_cards']))
        self.assertTrue(any(card['key'] == 'due_within_7_days_count' and card.get('query') == 'sla=warning' for card in payload['data']['focus_cards']))
        self.assertTrue(any(k['key'] == 'fulfillment_rate' for k in payload['data'].get('kpi_entries', [])))
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

    def test_dashboard_role_view_admin_should_allow_view_role_override(self):
        resp = self.client.get(reverse('api_dashboard_role_view') + '?view_role=warehouse_staff')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['data']['role'], 'warehouse_staff')
        self.assertEqual(payload['data']['view_type'], 'warehouse')

    def test_dashboard_role_view_non_admin_should_ignore_view_role_override(self):
        staff = User.objects.create_user(
            username='api_cs',
            password='test123',
            role='customer_service',
            is_staff=True,
        )
        self.client.logout()
        self.client.login(username='api_cs', password='test123')
        resp = self.client.get(reverse('api_dashboard_role_view') + '?view_role=admin')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['data']['role'], 'customer_service')
        self.assertEqual(payload['data']['view_type'], 'service')

    def test_dashboard_role_view_should_include_open_risk_events_entry(self):
        RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='medium',
            status='open',
            module='订单',
            title='API风险事件',
            description='测试',
            detected_by=self.user,
        )
        resp = self.client.get(reverse('api_dashboard_role_view'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertTrue(any(risk['key'] == 'open_risk_events' for risk in payload['data']['risk_entries']))

    def test_dashboard_role_view_should_include_overdue_approvals_entry(self):
        SystemSettings.objects.update_or_create(key='approval_pending_warn_hours', defaults={'value': '1'})
        task = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=1001,
            target_label='ORD-TEST',
            summary='测试待审批',
            payload={'order_id': 1001},
            requested_by=self.user,
        )
        ApprovalTask.objects.filter(id=task.id).update(created_at=timezone.now() - timedelta(hours=2))
        resp = self.client.get(reverse('api_dashboard_role_view'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertTrue(any(risk['key'] == 'overdue_pending_approvals' for risk in payload['data']['risk_entries']))

    def test_dashboard_kpi_trend_api_should_return_daily_buckets(self):
        today = timezone.localdate()
        Order.objects.create(
            order_no='ORD-TREND-DELIVERED',
            customer_name='趋势A',
            customer_phone='13800000002',
            delivery_address='广东省深圳市南山区',
            event_date=today - timedelta(days=1),
            status='delivered',
            total_amount=Decimal('100.00'),
            deposit_paid=Decimal('0.00'),
            balance=Decimal('100.00'),
            created_by=self.user,
        )
        Order.objects.create(
            order_no='ORD-TREND-CANCEL',
            customer_name='趋势B',
            customer_phone='13800000003',
            delivery_address='广东省深圳市南山区',
            event_date=today,
            status='cancelled',
            total_amount=Decimal('100.00'),
            deposit_paid=Decimal('0.00'),
            balance=Decimal('0.00'),
            created_by=self.user,
        )
        resp = self.client.get(reverse('api_dashboard_kpi_trend') + '?days=3')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['meta']['days'], 3)
        self.assertEqual(len(payload['data']), 3)
        self.assertTrue(any(int(row['cancelled']) >= 1 for row in payload['data']))
        self.assertTrue(any(int(row['delivered']) >= 1 for row in payload['data']))

    def test_ops_alerts_api_should_return_summary_and_filtered_alerts(self):
        task = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=1001,
            target_label='ORD-TEST',
            summary='测试待审批',
            payload={'order_id': 1001},
            requested_by=self.user,
        )
        ApprovalTask.objects.filter(id=task.id).update(created_at=timezone.now() - timedelta(hours=26))
        RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='medium',
            status='open',
            module='订单',
            title='API风险事件',
            description='测试',
            detected_by=self.user,
        )
        DataConsistencyCheckRun.objects.create(
            source='manual',
            total_issues=2,
            summary={'total': 2},
            issues=[{'type': 'finance_reconciliation_mismatch', 'x': 1}],
            executed_by=self.user,
        )
        resp = self.client.get(reverse('api_ops_alerts'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertIn('summary', payload['data'])
        self.assertIn('alerts', payload['data'])
        self.assertTrue(any(a['title'] == '审批任务超时' for a in payload['data']['alerts']))
        self.assertTrue(any(a['source'] == 'finance' for a in payload['data']['alerts']))
        self.assertEqual(payload['data']['summary']['finance_mismatch_count'], 1)

        resp2 = self.client.get(reverse('api_ops_alerts') + '?source=approval&severity=danger')
        self.assertEqual(resp2.status_code, 200)
        alerts = resp2.json()['data']['alerts']
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['source'], 'approval')
        self.assertEqual(alerts[0]['severity'], 'danger')

    def test_risk_events_api_should_support_filters(self):
        event_mine = RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='high',
            status='processing',
            module='订单',
            title='我的风险事件',
            description='需要处理',
            detected_by=self.user,
            assignee=self.user,
        )
        other = User.objects.create_user(
            username='api_risk_other',
            password='test123',
            role='manager',
            is_staff=True,
        )
        RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='low',
            status='open',
            module='订单',
            title='其他风险事件',
            description='其他',
            detected_by=self.user,
            assignee=other,
        )
        resp = self.client.get(reverse('api_risk_events') + '?mine_only=1&status=processing')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['meta']['total'], 1)
        self.assertEqual(payload['data'][0]['id'], event_mine.id)
        self.assertEqual(payload['data'][0]['assignee_name'], self.user.full_name or self.user.username)

    def test_approvals_api_should_support_filters(self):
        mine = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=1001,
            target_label='ORD-MINE',
            summary='我发起审批',
            payload={'order_id': 1001},
            requested_by=self.user,
            status='pending',
        )
        other_user = User.objects.create_user(
            username='api_approval_other',
            password='test123',
            role='manager',
            is_staff=True,
        )
        other = ApprovalTask.objects.create(
            action_code='transfer.cancel_task',
            module='转寄',
            target_type='transfer',
            target_id=1,
            target_label='TR-1',
            summary='他人发起审批',
            payload={'transfer_id': 1},
            requested_by=other_user,
            status='pending',
        )
        resp = self.client.get(reverse('api_approvals') + '?reviewable_only=1')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['meta']['total'], 1)
        self.assertEqual(payload['data'][0]['id'], other.id)
        self.assertNotEqual(payload['data'][0]['id'], mine.id)

    def test_transfer_recommendation_logs_api_should_support_filters(self):
        order = Order.objects.get(id=1001)
        OrderItem.objects.create(
            order=order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=self.sku.rental_price,
        )
        log = TransferRecommendationLog.objects.create(
            order=order,
            sku=self.sku,
            trigger_type='recommend',
            target_event_date=order.event_date,
            target_address=order.delivery_address,
            before_source_order_ids=[111],
            selected_source_order_id=222,
            selected_source_order_no='ORD-SOURCE-222',
            warehouse_needed=0,
            candidates=[{'order_no': 'ORD-SOURCE-222'}],
            score_summary={'candidate_count': 1},
            operator=self.user,
        )
        resp = self.client.get(reverse('api_transfer_recommendation_logs') + '?trigger_type=recommend&keyword=API客户')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['meta']['total'], 1)
        self.assertEqual(payload['data'][0]['id'], log.id)
        self.assertEqual(payload['data'][0]['trigger_type_display'], '转寄中心重推')
        # 仓库补量过滤
        TransferRecommendationLog.objects.create(
            order=order,
            sku=self.sku,
            trigger_type='recommend',
            target_event_date=order.event_date,
            target_address=order.delivery_address,
            before_source_order_ids=[],
            selected_source_order_id=None,
            selected_source_order_no='',
            warehouse_needed=1,
            candidates=[],
            score_summary={'candidate_count': 0},
            operator=self.user,
        )
        resp2 = self.client.get(reverse('api_transfer_recommendation_logs') + '?decision_type=warehouse')
        self.assertEqual(resp2.status_code, 200)
        payload2 = resp2.json()
        self.assertTrue(payload2['success'])
        self.assertEqual(payload2['meta']['decision_type'], 'warehouse')
        self.assertTrue(all((item.get('selected_source_order_id') in (None, 0)) for item in payload2['data']))

    def test_transfer_recommendation_log_detail_api_should_return_single_log(self):
        order = Order.objects.get(id=1001)
        OrderItem.objects.create(
            order=order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=self.sku.rental_price,
        )
        log = TransferRecommendationLog.objects.create(
            order=order,
            sku=self.sku,
            trigger_type='recommend',
            target_event_date=order.event_date,
            target_address=order.delivery_address,
            before_source_order_ids=[111],
            selected_source_order_id=222,
            selected_source_order_no='ORD-SOURCE-222',
            warehouse_needed=0,
            candidates=[{'source_order_no': 'ORD-SOURCE-222', 'score_total': 12.3}],
            score_summary={'candidate_count': 1, 'selected_rank': 1},
            operator=self.user,
        )
        resp = self.client.get(reverse('api_transfer_recommendation_log_detail', kwargs={'log_id': log.id}))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['data']['id'], log.id)
        self.assertEqual(payload['data']['order_no'], order.order_no)
        self.assertEqual(payload['data']['score_summary']['selected_rank'], 1)

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
