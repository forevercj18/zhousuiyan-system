"""
业务工具函数
包含：系统设置获取、库存校验、排期计算、转寄匹配等
"""
from datetime import timedelta
from decimal import Decimal
from difflib import SequenceMatcher
import math
import re
from django.db.models import Sum, Q, Count, F
from django.utils import timezone
from .models import (
    SystemSettings,
    Order,
    OrderItem,
    Reservation,
    User,
    SKU,
    Transfer,
    TransferAllocation,
    InventoryUnit,
    Part,
    MaintenanceWorkOrder,
    PartRecoveryInspection,
    RiskEvent,
    ApprovalTask,
    DataConsistencyCheckRun,
)


def build_finance_reconciliation_rows(
    status_filter='',
    keyword='',
    mismatch_only=False,
    mismatch_field='',
    min_diff_amount=Decimal('0.00'),
):
    """财务对账行构建（页面/API/巡检复用）。"""
    status_filter = (status_filter or '').strip()
    keyword = (keyword or '').strip()
    mismatch_field = (mismatch_field or '').strip()
    try:
        min_diff_amount = Decimal(str(min_diff_amount or '0'))
    except Exception:
        min_diff_amount = Decimal('0.00')
    if min_diff_amount < Decimal('0.00'):
        min_diff_amount = Decimal('0.00')
    orders = Order.objects.prefetch_related('items').annotate(
        tx_deposit_received=Sum(
            'finance_transactions__amount',
            filter=Q(finance_transactions__transaction_type__in=['deposit_received', 'reservation_deposit_applied'])
        ),
        tx_balance_received=Sum('finance_transactions__amount', filter=Q(finance_transactions__transaction_type='balance_received')),
        tx_deposit_refund=Sum('finance_transactions__amount', filter=Q(finance_transactions__transaction_type='deposit_refund')),
        tx_penalty=Sum('finance_transactions__amount', filter=Q(finance_transactions__transaction_type='penalty_charge')),
    ).order_by('-created_at')
    if status_filter:
        orders = orders.filter(status=status_filter)
    if keyword:
        orders = orders.filter(
            Q(order_no__icontains=keyword) |
            Q(customer_name__icontains=keyword) |
            Q(customer_phone__icontains=keyword)
        )

    rows = []
    for order in orders:
        expected_deposit = Decimal('0.00')
        for item in order.items.all():
            expected_deposit += (item.deposit or Decimal('0.00')) * Decimal(int(item.quantity or 0))
        tx_deposit_received = order.tx_deposit_received or Decimal('0.00')
        tx_balance_received = order.tx_balance_received or Decimal('0.00')
        tx_deposit_refund = order.tx_deposit_refund or Decimal('0.00')
        tx_penalty = order.tx_penalty or Decimal('0.00')
        expected_balance_received = (order.total_amount or Decimal('0.00')) - (order.balance or Decimal('0.00'))
        expected_refund = (order.deposit_paid or Decimal('0.00')) - tx_penalty
        if expected_refund < Decimal('0.00'):
            expected_refund = Decimal('0.00')

        deposit_diff = tx_deposit_received - (order.deposit_paid or Decimal('0.00'))
        balance_diff = tx_balance_received - expected_balance_received
        refund_diff = tx_deposit_refund - expected_refund if order.status in ['completed', 'cancelled'] else Decimal('0.00')
        has_mismatch = any(abs(v) > Decimal('0.01') for v in [deposit_diff, balance_diff, refund_diff])
        suggestions = []
        if deposit_diff < Decimal('-0.01'):
            suggestions.append('押金少收，建议补录收押金流水')
        elif deposit_diff > Decimal('0.01'):
            suggestions.append('押金多收，建议核对并补录退押/人工调整')
        if balance_diff < Decimal('-0.01'):
            suggestions.append('尾款少收，建议补录收尾款流水')
        elif balance_diff > Decimal('0.01'):
            suggestions.append('尾款多收，建议核对是否重复记账')
        if refund_diff < Decimal('-0.01'):
            suggestions.append('退押不足，建议补录退押流水')
        elif refund_diff > Decimal('0.01'):
            suggestions.append('退押超额，建议核对扣罚与退款口径')
        if has_mismatch and not suggestions:
            suggestions.append('账务差异需人工复核')

        mismatch_fields = []
        if abs(deposit_diff) > Decimal('0.01'):
            mismatch_fields.append('deposit')
        if abs(balance_diff) > Decimal('0.01'):
            mismatch_fields.append('balance')
        if abs(refund_diff) > Decimal('0.01'):
            mismatch_fields.append('refund')

        rows.append({
            'order': order,
            'expected_deposit': expected_deposit,
            'tx_deposit_received': tx_deposit_received,
            'deposit_diff': deposit_diff,
            'expected_balance_received': expected_balance_received,
            'tx_balance_received': tx_balance_received,
            'balance_diff': balance_diff,
            'expected_refund': expected_refund,
            'tx_deposit_refund': tx_deposit_refund,
            'refund_diff': refund_diff,
            'tx_penalty': tx_penalty,
            'has_mismatch': has_mismatch,
            'mismatch_fields': mismatch_fields,
            'suggestions': suggestions,
        })
    if mismatch_only:
        rows = [r for r in rows if r['has_mismatch']]
    if mismatch_field in ['deposit', 'balance', 'refund']:
        rows = [r for r in rows if mismatch_field in (r.get('mismatch_fields') or [])]
    if min_diff_amount > Decimal('0.00'):
        rows = [
            r for r in rows
            if max(
                abs(r.get('deposit_diff') or Decimal('0.00')),
                abs(r.get('balance_diff') or Decimal('0.00')),
                abs(r.get('refund_diff') or Decimal('0.00')),
            ) >= min_diff_amount
        ]
    return rows


def get_system_settings():
    """获取系统设置（返回字典）"""
    settings = {}
    for item in SystemSettings.objects.all():
        try:
            # 尝试转换为整数
            settings[item.key] = int(item.value)
        except ValueError:
            settings[item.key] = item.value

    # 设置默认值
    settings.setdefault('ship_lead_days', 2)
    settings.setdefault('return_offset_days', 1)
    settings.setdefault('reservation_followup_lead_days', 7)
    settings.setdefault('buffer_days', 1)
    settings.setdefault('max_transfer_gap_days', 3)
    settings.setdefault('warehouse_sender_name', '仓库发货员')
    settings.setdefault('warehouse_sender_phone', '')
    settings.setdefault('warehouse_sender_address', '仓库地址未配置')
    settings.setdefault('transfer_pending_timeout_hours', 24)
    settings.setdefault('transfer_shipped_timeout_days', 3)
    settings.setdefault('outbound_max_days_warn', 10)
    settings.setdefault('outbound_max_hops_warn', 4)
    settings.setdefault('approval_pending_warn_hours', 24)
    settings.setdefault('approval_required_count_default', 1)
    settings.setdefault('approval_required_count_order_force_cancel', 1)
    settings.setdefault('approval_required_count_transfer_cancel_task', 1)
    settings.setdefault('approval_required_count_unit_dispose', 1)
    settings.setdefault('approval_required_count_unit_disassemble', 1)
    settings.setdefault('approval_required_count_unit_scrap', 1)
    settings.setdefault('approval_required_count_map', '{}')
    settings.setdefault('alert_notify_enabled', 0)
    settings.setdefault('alert_notify_webhook_url', '')
    settings.setdefault('alert_notify_min_severity', 'warning')
    settings.setdefault('page_size_default', 10)
    settings.setdefault('page_size_transfer_candidates', 5)
    settings.setdefault('page_size_outbound_topology_units', 5)
    settings.setdefault('transfer_score_weight_date', 100)
    settings.setdefault('transfer_score_weight_confidence', 10)
    settings.setdefault('transfer_score_weight_distance', 1)

    return settings


def get_dashboard_stats_payload(include_transfer_available=False):
    """统一工作台统计口径（页面/API共用）。"""
    total_stock = sum(
        sku.effective_stock
        for sku in SKU.objects.filter(is_active=True).only('id', 'stock')
    )
    occupied_raw = OrderItem.objects.filter(
        order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
        sku__is_active=True
    ).aggregate(total=Sum('quantity'))['total'] or 0
    transfer_allocated = TransferAllocation.objects.filter(
        target_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
        source_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
        sku__is_active=True,
        status__in=['locked', 'consumed']
    ).aggregate(total=Sum('quantity'))['total'] or 0
    occupied_stock = max(occupied_raw - transfer_allocated, 0)
    warehouse_available_stock = max(total_stock - occupied_stock, 0)
    total_revenue = Order.objects.filter(status='completed').aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    pending_revenue = Order.objects.exclude(status__in=['completed', 'cancelled']).aggregate(total=Sum('balance'))['total'] or Decimal('0.00')

    pending_orders_count = Order.objects.filter(status='pending').count()
    delivered_orders_count = Order.objects.filter(status='delivered').count()
    completed_orders_count = Order.objects.filter(status='completed').count()
    cancelled_orders_count = Order.objects.filter(status='cancelled').count()
    total_orders_count = Order.objects.count()
    fulfillment_rate = round((completed_orders_count / total_orders_count) * 100, 1) if total_orders_count else 0.0
    cancel_rate = round((cancelled_orders_count / total_orders_count) * 100, 1) if total_orders_count else 0.0
    completed_orders = Order.objects.filter(status='completed', ship_date__isnull=False, return_date__isnull=False)
    transit_days = []
    for o in completed_orders.only('ship_date', 'return_date'):
        if o.return_date and o.ship_date:
            transit_days.append(max((o.return_date - o.ship_date).days, 0))
    avg_transit_days = round(sum(transit_days) / len(transit_days), 1) if transit_days else 0.0
    warning_cutoff = today = timezone.localdate()
    warning_end = warning_cutoff + timedelta(days=7)
    shipped_statuses = ['delivered', 'in_use', 'returned', 'completed']
    due_within_7_days_count = (
        Order.objects.filter(
            ship_date__gt=warning_cutoff,
            ship_date__lte=warning_end,
        )
        .exclude(
            Q(status__in=shipped_statuses) |
            Q(ship_tracking__isnull=False, ship_tracking__gt='')
        )
        .count()
    )
    overdue_orders_count = (
        Order.objects.filter(
            ship_date__isnull=False,
            ship_date__lte=warning_cutoff,
        )
        .exclude(
            Q(status__in=shipped_statuses) |
            Q(ship_tracking__isnull=False, ship_tracking__gt='')
        )
        .count()
    )
    ready_reservations_count = Reservation.objects.filter(status='ready_to_convert').count()

    stats = {
        'pending_orders': pending_orders_count,
        'delivered_orders': delivered_orders_count,
        'completed_orders': completed_orders_count,
        'cancelled_orders': cancelled_orders_count,
        'warehouse_available_stock': warehouse_available_stock,
        'total_orders': total_orders_count,
        'total_skus': SKU.objects.filter(is_active=True).count(),
        'low_stock_parts': Part.objects.filter(is_active=True, current_stock__lt=F('safety_stock')).count(),
        'total_revenue': total_revenue,
        'pending_revenue': pending_revenue,
        'open_risk_events': RiskEvent.objects.filter(status='open').count(),
        'fulfillment_rate': fulfillment_rate,
        'cancel_rate': cancel_rate,
        'avg_transit_days': avg_transit_days,
        'due_within_7_days_count': due_within_7_days_count,
        'overdue_orders_count': overdue_orders_count,
        'ready_reservations_count': ready_reservations_count,
        'pending_recovery_inspections': PartRecoveryInspection.objects.filter(status='pending').count(),
        'repair_recovery_inspections': PartRecoveryInspection.objects.filter(status='repair').count(),
        'draft_maintenance_work_orders': MaintenanceWorkOrder.objects.filter(status='draft').count(),
    }
    if include_transfer_available:
        stats['transfer_available_count'] = len(find_transfer_candidates())
    return stats


def get_reservation_followup_counts(owner=None):
    settings = get_system_settings()
    lead_days = int(settings.get('reservation_followup_lead_days', 7) or 7)
    target_event_date = timezone.localdate() + timedelta(days=lead_days)
    qs = Reservation.objects.filter(status__in=['pending_info', 'ready_to_convert'])
    if owner is not None:
        qs = qs.filter(owner=owner)
    return {
        'lead_days': lead_days,
        'today_count': qs.filter(event_date=target_event_date).count(),
        'overdue_count': qs.filter(event_date__lt=target_event_date).count(),
    }


def get_reservation_post_convert_counts(owner=None):
    qs = Reservation.objects.filter(
        status='converted',
        converted_order__status__in=['pending', 'confirmed'],
    )
    if owner is not None:
        qs = qs.filter(owner=owner)
    return {
        'awaiting_shipment_count': qs.count(),
    }


def get_reservation_converted_followup_counts(owner=None):
    today = timezone.localdate()
    qs = Reservation.objects.filter(status='converted', converted_order__isnull=False).exclude(
        converted_order__status='cancelled',
    )
    if owner is not None:
        qs = qs.filter(owner=owner)
    overdue_shipment_qs = qs.filter(
        converted_order__status__in=['pending', 'confirmed'],
        converted_order__ship_date__isnull=False,
        converted_order__ship_date__lte=today,
        converted_order__ship_tracking='',
    )
    balance_due_qs = qs.filter(converted_order__balance__gt=Decimal('0.00'))
    return {
        'overdue_shipment_count': overdue_shipment_qs.count(),
        'balance_due_count': balance_due_qs.count(),
    }


def get_reservation_owner_followup_panels():
    settings = get_system_settings()
    lead_days = int(settings.get('reservation_followup_lead_days', 7) or 7)
    today = timezone.localdate()
    target_event_date = today + timedelta(days=lead_days)

    today_rows = Reservation.objects.filter(
        status__in=['pending_info', 'ready_to_convert'],
        event_date=target_event_date,
        owner__isnull=False,
    ).values('owner_id').annotate(total=Count('id'))
    overdue_rows = Reservation.objects.filter(
        status__in=['pending_info', 'ready_to_convert'],
        event_date__lt=target_event_date,
        owner__isnull=False,
    ).values('owner_id').annotate(total=Count('id'))
    overdue_shipment_rows = Reservation.objects.filter(
        status='converted',
        owner__isnull=False,
        converted_order__status__in=['pending', 'confirmed'],
        converted_order__ship_date__isnull=False,
        converted_order__ship_date__lte=today,
        converted_order__ship_tracking='',
    ).values('owner_id').annotate(total=Count('id'))
    balance_due_rows = Reservation.objects.filter(
        status='converted',
        owner__isnull=False,
        converted_order__isnull=False,
        converted_order__balance__gt=Decimal('0.00'),
    ).exclude(
        converted_order__status='cancelled',
    ).values('owner_id').annotate(total=Count('id'))

    owner_map = {}
    for rows, key in [
        (today_rows, 'today_count'),
        (overdue_rows, 'overdue_count'),
        (overdue_shipment_rows, 'overdue_shipment_count'),
        (balance_due_rows, 'balance_due_count'),
    ]:
        for row in rows:
            owner_map.setdefault(row['owner_id'], {
                'today_count': 0,
                'overdue_count': 0,
                'overdue_shipment_count': 0,
                'balance_due_count': 0,
            })[key] = int(row['total'] or 0)

    if not owner_map:
        return []

    owners = User.objects.filter(id__in=owner_map.keys(), is_active=True).order_by('full_name', 'username')
    panels = []
    for owner in owners:
        counts = owner_map.get(owner.id, {})
        total = sum(int(counts.get(key) or 0) for key in ['today_count', 'overdue_count', 'overdue_shipment_count', 'balance_due_count'])
        if total <= 0:
            continue
        panels.append({
            'owner_id': owner.id,
            'owner_name': owner.full_name or owner.get_full_name() or owner.username,
            'today_count': int(counts.get('today_count') or 0),
            'overdue_count': int(counts.get('overdue_count') or 0),
            'overdue_shipment_count': int(counts.get('overdue_shipment_count') or 0),
            'balance_due_count': int(counts.get('balance_due_count') or 0),
            'total': total,
        })
    panels.sort(key=lambda item: (-item['total'], item['owner_name']))
    return panels


def get_reservation_owner_transfer_suggestions():
    owner_panels = get_reservation_owner_followup_panels()
    owner_map = {panel['owner_id']: panel for panel in owner_panels}
    candidate_owners = list(
        User.objects.filter(
            is_active=True,
            role='customer_service',
        ).order_by('full_name', 'username')
    )
    for owner in candidate_owners:
        owner_map.setdefault(owner.id, {
            'owner_id': owner.id,
            'owner_name': owner.full_name or owner.get_full_name() or owner.username,
            'today_count': 0,
            'overdue_count': 0,
            'overdue_shipment_count': 0,
            'balance_due_count': 0,
            'total': 0,
        })
    owner_panels = list(owner_map.values())
    if len(owner_panels) < 2:
        return []

    owners = {
        owner.id: owner for owner in candidate_owners
    }
    avg_load = sum(panel['total'] for panel in owner_panels) / len(owner_panels)
    low_load_panels = sorted(owner_panels, key=lambda item: (item['total'], item['owner_name']))
    suggestions = []

    for source in owner_panels:
        if source['total'] < 3 or source['total'] <= avg_load:
            continue
        target = next(
            (
                panel for panel in low_load_panels
                if panel['owner_id'] != source['owner_id'] and panel['total'] + 1 < source['total']
            ),
            None,
        )
        if not target:
            continue

        suggest_count = max(1, min(3, (source['total'] - target['total']) // 2 or 1))
        candidate_qs = Reservation.objects.filter(owner_id=source['owner_id']).select_related('converted_order').order_by('created_at')
        today = timezone.localdate()
        settings = get_system_settings()
        target_event_date = today + timedelta(days=int(settings.get('reservation_followup_lead_days', 7) or 7))
        priority_map = []
        for reservation in candidate_qs:
            priority = 99
            if reservation.status in ['pending_info', 'ready_to_convert'] and reservation.event_date < target_event_date:
                priority = 0
            elif reservation.status == 'converted' and reservation.converted_order_id and reservation.converted_order.status in ['pending', 'confirmed']:
                if reservation.converted_order.ship_date and reservation.converted_order.ship_date <= today and not reservation.converted_order.ship_tracking:
                    priority = 1
                elif (reservation.converted_order.balance or Decimal('0.00')) > Decimal('0.00'):
                    priority = 2
                else:
                    priority = 3
            elif reservation.status in ['pending_info', 'ready_to_convert'] and reservation.event_date == target_event_date:
                priority = 4
            if priority < 99:
                priority_map.append((priority, reservation))
        priority_map.sort(key=lambda item: (item[0], item[1].created_at))
        samples = [item[1] for item in priority_map[:suggest_count]]
        if not samples:
            continue
        suggestions.append({
            'source_owner_id': source['owner_id'],
            'source_owner_name': source['owner_name'],
            'target_owner_id': target['owner_id'],
            'target_owner_name': target['owner_name'],
            'suggest_count': len(samples),
            'source_total': source['total'],
            'target_total': target['total'],
            'reservation_samples': [reservation.reservation_no for reservation in samples],
            'source_query': f"owner={source['owner_id']}",
        })

    return suggestions


def get_role_dashboard_payload(user, view_role=None, base_stats=None):
    """
    角色看板数据层V1（只读聚合）：
    - 输出基础统计
    - 根据角色输出推荐关注卡片
    """
    base = base_stats or get_dashboard_stats_payload(include_transfer_available=True)
    role = (view_role or getattr(user, 'role', 'warehouse_staff') or 'warehouse_staff').strip()
    valid_roles = {'admin', 'manager', 'warehouse_manager', 'warehouse_staff', 'customer_service'}
    if role not in valid_roles:
        role = getattr(user, 'role', 'warehouse_staff')
    reservation_followup = get_reservation_followup_counts(
        None if role in ['admin', 'manager'] else user
    )
    reservation_post_convert = get_reservation_post_convert_counts(
        None if role in ['admin', 'manager'] else user
    )
    reservation_converted_followup = get_reservation_converted_followup_counts(
        None if role in ['admin', 'manager'] else user
    )
    reservation_owner_panels = get_reservation_owner_followup_panels() if role in ['admin', 'manager'] else []
    reservation_owner_transfer_suggestions = get_reservation_owner_transfer_suggestions() if role in ['admin', 'manager'] else []

    pending_transfer_tasks = Transfer.objects.filter(status='pending').count()
    settings = get_system_settings()
    transfer_pending_timeout_hours = int(settings.get('transfer_pending_timeout_hours', 24) or 24)
    transfer_cutoff = timezone.now() - timedelta(hours=transfer_pending_timeout_hours)
    overdue_pending_transfers = Transfer.objects.filter(status='pending', created_at__lt=transfer_cutoff).count()
    approval_warn_hours = int(settings.get('approval_pending_warn_hours', 24) or 24)
    approval_cutoff = timezone.now() - timedelta(hours=approval_warn_hours)
    overdue_pending_approvals = ApprovalTask.objects.filter(status='pending', created_at__lt=approval_cutoff).count()

    risk_entries = [
        {
            'key': 'pending_transfer_tasks',
            'label': '待执行转寄',
            'value': pending_transfer_tasks,
            'url_name': 'transfers_list',
            'query': 'panel=tasks&status=pending',
            'severity': 'warning',
        },
        {
            'key': 'low_stock_parts',
            'label': '低库存部件',
            'value': base['low_stock_parts'],
            'url_name': 'parts_inventory_list',
            'query': 'low=1',
            'severity': 'danger',
        },
    ]
    if base.get('open_risk_events', 0) > 0:
        risk_entries.insert(0, {
            'key': 'open_risk_events',
            'label': '待处理风险事件',
            'value': base['open_risk_events'],
            'url_name': 'risk_events_list',
            'query': 'status=open',
            'severity': 'danger',
        })
    if overdue_pending_approvals > 0:
        risk_entries.insert(0, {
            'key': 'overdue_pending_approvals',
            'label': '超时待审批',
            'value': overdue_pending_approvals,
            'url_name': 'approvals_list',
            'query': 'status=pending',
            'severity': 'danger',
        })
    if overdue_pending_transfers > 0:
        risk_entries.insert(0, {
            'key': 'overdue_pending_transfers',
            'label': '超时待执行转寄',
            'value': overdue_pending_transfers,
            'url_name': 'transfers_list',
            'query': 'panel=tasks&status=pending',
            'severity': 'danger',
        })

    if role in ['admin', 'manager']:
        view_type = 'business'
        warehouse_insights = []
        focus_cards = [
            {'key': 'total_revenue', 'label': '总营收', 'value': base['total_revenue'], 'url_name': 'finance_transactions_list', 'query': ''},
            {'key': 'pending_revenue', 'label': '待收款', 'value': base['pending_revenue'], 'url_name': 'orders_list', 'query': ''},
            {'key': 'pending_orders', 'label': '待处理订单', 'value': base['pending_orders'], 'url_name': 'orders_list', 'query': 'status=pending'},
            {'key': 'ready_reservations_count', 'label': '待转正式订单', 'value': base['ready_reservations_count'], 'url_name': 'reservations_list', 'query': 'status=ready_to_convert'},
            {'key': 'converted_pending_shipment_reservations_count', 'label': '转单待发货', 'value': reservation_post_convert['awaiting_shipment_count'], 'url_name': 'reservations_list', 'query': 'journey=awaiting_shipment'},
            {'key': 'converted_overdue_shipment_reservations_count', 'label': '待发货超时', 'value': reservation_converted_followup['overdue_shipment_count'], 'url_name': 'reservations_list', 'query': 'journey=awaiting_shipment_overdue'},
            {'key': 'converted_balance_due_reservations_count', 'label': '待收尾款', 'value': reservation_converted_followup['balance_due_count'], 'url_name': 'reservations_list', 'query': 'journey=balance_due'},
            {'key': 'today_followup_reservations_count', 'label': '今日需联系预定单', 'value': reservation_followup['today_count'], 'url_name': 'reservations_list', 'query': 'contact=today'},
            {'key': 'overdue_followup_reservations_count', 'label': '逾期未联系预定单', 'value': reservation_followup['overdue_count'], 'url_name': 'reservations_list', 'query': 'contact=overdue'},
            {'key': 'overdue_orders_count', 'label': '已超时订单', 'value': base['overdue_orders_count'], 'url_name': 'orders_list', 'query': 'sla=overdue'},
            {'key': 'due_within_7_days_count', 'label': '7天内到期', 'value': base['due_within_7_days_count'], 'url_name': 'orders_list', 'query': 'sla=warning'},
            {'key': 'completed_orders', 'label': '已完成订单', 'value': base['completed_orders'], 'url_name': 'orders_list', 'query': 'status=completed'},
            {'key': 'transfer_available_count', 'label': '可转寄候选', 'value': base['transfer_available_count'], 'url_name': 'transfers_list', 'query': 'panel=candidates'},
        ]
        quick_actions = [
            {'label': '新建订单', 'url_name': 'order_create', 'style': 'primary'},
            {'label': '新建预定单', 'url_name': 'reservation_create', 'style': 'outline-secondary'},
            {'label': '订单中心', 'url_name': 'orders_list', 'style': 'outline-secondary'},
            {'label': '转寄中心', 'url_name': 'transfers_list', 'style': 'outline-secondary'},
            {'label': '查看排期', 'url_name': 'calendar', 'style': 'outline-secondary'},
            {'label': '创建采购单', 'url_name': 'purchase_order_create', 'style': 'outline-secondary'},
        ]
        risk_entries.insert(0, {
            'key': 'pending_orders',
            'label': '待处理订单',
            'value': base['pending_orders'],
            'url_name': 'orders_list',
            'query': 'status=pending',
            'severity': 'info',
        })
    elif role in ['warehouse_manager', 'warehouse_staff']:
        view_type = 'warehouse'
        top_issue_parts = list(
            Part.objects.filter(is_active=True).annotate(
                issue_rows=Count(
                    'inventory_unit_parts',
                    filter=Q(
                        inventory_unit_parts__is_active=True,
                        inventory_unit_parts__status__in=['missing', 'damaged', 'lost'],
                    )
                )
            ).filter(issue_rows__gt=0).order_by('-issue_rows', 'name')[:3]
        )
        top_recovery_parts = list(
            Part.objects.filter(is_active=True).annotate(
                pending_qty=Sum('recovery_inspections__quantity', filter=Q(recovery_inspections__status='pending')),
                repair_qty=Sum('recovery_inspections__quantity', filter=Q(recovery_inspections__status='repair')),
            ).filter(
                Q(pending_qty__gt=0) | Q(repair_qty__gt=0)
            ).order_by('-pending_qty', '-repair_qty', 'name')[:3]
        )
        focus_cards = [
            {'key': 'warehouse_available_stock', 'label': '仓库可用库存', 'value': base['warehouse_available_stock'], 'url_name': 'skus_list', 'query': ''},
            {'key': 'low_stock_parts', 'label': '低库存部件', 'value': base['low_stock_parts'], 'url_name': 'parts_inventory_list', 'query': 'low=1'},
            {'key': 'pending_recovery_inspections', 'label': '待质检回件', 'value': base['pending_recovery_inspections'], 'url_name': 'part_recovery_inspections_list', 'query': 'status=pending'},
            {'key': 'repair_recovery_inspections', 'label': '待维修回件', 'value': base['repair_recovery_inspections'], 'url_name': 'part_recovery_inspections_list', 'query': 'status=repair'},
            {'key': 'draft_maintenance_work_orders', 'label': '待执行维修单', 'value': base['draft_maintenance_work_orders'], 'url_name': 'maintenance_work_orders_list', 'query': 'status=draft'},
            {'key': 'pending_transfer_tasks', 'label': '待执行转寄任务', 'value': pending_transfer_tasks, 'url_name': 'transfers_list', 'query': 'panel=tasks&status=pending'},
            {'key': 'overdue_orders_count', 'label': '已超时订单', 'value': base['overdue_orders_count'], 'url_name': 'orders_list', 'query': 'sla=overdue'},
            {'key': 'due_within_7_days_count', 'label': '7天内到期', 'value': base['due_within_7_days_count'], 'url_name': 'orders_list', 'query': 'sla=warning'},
            {'key': 'delivered_orders', 'label': '已发货订单', 'value': base['delivered_orders'], 'url_name': 'orders_list', 'query': 'status=delivered'},
        ]
        quick_actions = [
            {'label': '订单中心', 'url_name': 'orders_list', 'style': 'primary'},
            {'label': '转寄中心', 'url_name': 'transfers_list', 'style': 'outline-secondary'},
            {'label': '产品管理', 'url_name': 'skus_list', 'style': 'outline-secondary'},
            {'label': '在外库存', 'url_name': 'outbound_inventory_dashboard', 'style': 'outline-secondary'},
            {'label': '部件库存', 'url_name': 'parts_inventory_list', 'style': 'outline-secondary'},
            {'label': '仓储报表', 'url_name': 'warehouse_reports', 'style': 'outline-secondary'},
        ]
        risk_entries.insert(0, {
            'key': 'pending_orders',
            'label': '待处理订单',
            'value': base['pending_orders'],
            'url_name': 'orders_list',
            'query': 'status=pending',
            'severity': 'info',
        })
        warehouse_insights = []
        if top_issue_parts:
            warehouse_insights.append({
                'title': '高频异常部件',
                'items': [
                    {
                        'label': part.name,
                        'value': f"{part.issue_rows} 条异常",
                        'url_name': 'warehouse_reports',
                        'query': f'part_id={part.id}&range=30',
                        'detail_url_name': 'part_issue_pool',
                        'detail_query': f'keyword={part.name}',
                    }
                    for part in top_issue_parts
                ]
            })
        if top_recovery_parts:
            warehouse_insights.append({
                'title': '回件待处理焦点',
                'items': [
                    {
                        'label': part.name,
                        'value': f"待质检 {part.pending_qty or 0} / 待维修 {part.repair_qty or 0}",
                        'url_name': 'warehouse_reports',
                        'query': f'part_id={part.id}&range=30',
                        'detail_url_name': 'part_recovery_inspections_list',
                        'detail_query': f'keyword={part.name}',
                    }
                    for part in top_recovery_parts
                ]
            })
    else:
        view_type = 'service'
        warehouse_insights = []
        focus_cards = [
            {'key': 'pending_orders', 'label': '待处理订单', 'value': base['pending_orders'], 'url_name': 'orders_list', 'query': 'status=pending'},
            {'key': 'ready_reservations_count', 'label': '待转正式订单', 'value': base['ready_reservations_count'], 'url_name': 'reservations_list', 'query': 'status=ready_to_convert'},
            {'key': 'converted_pending_shipment_reservations_count', 'label': '转单待发货', 'value': reservation_post_convert['awaiting_shipment_count'], 'url_name': 'reservations_list', 'query': 'journey=awaiting_shipment'},
            {'key': 'converted_overdue_shipment_reservations_count', 'label': '待发货超时', 'value': reservation_converted_followup['overdue_shipment_count'], 'url_name': 'reservations_list', 'query': 'journey=awaiting_shipment_overdue'},
            {'key': 'converted_balance_due_reservations_count', 'label': '待收尾款', 'value': reservation_converted_followup['balance_due_count'], 'url_name': 'reservations_list', 'query': 'journey=balance_due'},
            {'key': 'today_followup_reservations_count', 'label': '今日需联系预定单', 'value': reservation_followup['today_count'], 'url_name': 'reservations_list', 'query': 'contact=today'},
            {'key': 'overdue_followup_reservations_count', 'label': '逾期未联系预定单', 'value': reservation_followup['overdue_count'], 'url_name': 'reservations_list', 'query': 'contact=overdue'},
            {'key': 'overdue_orders_count', 'label': '已超时订单', 'value': base['overdue_orders_count'], 'url_name': 'orders_list', 'query': 'sla=overdue'},
            {'key': 'due_within_7_days_count', 'label': '7天内到期', 'value': base['due_within_7_days_count'], 'url_name': 'orders_list', 'query': 'sla=warning'},
            {'key': 'delivered_orders', 'label': '已发货订单', 'value': base['delivered_orders'], 'url_name': 'orders_list', 'query': 'status=delivered'},
            {'key': 'pending_revenue', 'label': '待收款', 'value': base['pending_revenue'], 'url_name': 'orders_list', 'query': ''},
            {'key': 'total_orders', 'label': '订单总数', 'value': base['total_orders'], 'url_name': 'orders_list', 'query': ''},
        ]
        quick_actions = [
            {'label': '新建订单', 'url_name': 'order_create', 'style': 'primary'},
            {'label': '新建预定单', 'url_name': 'reservation_create', 'style': 'outline-secondary'},
            {'label': '订单中心', 'url_name': 'orders_list', 'style': 'outline-secondary'},
            {'label': '预定管理', 'url_name': 'reservations_list', 'style': 'outline-secondary'},
            {'label': '转寄中心', 'url_name': 'transfers_list', 'style': 'outline-secondary'},
            {'label': '查看排期', 'url_name': 'calendar', 'style': 'outline-secondary'},
        ]
        risk_entries.insert(0, {
            'key': 'pending_orders',
            'label': '待处理订单',
            'value': base['pending_orders'],
            'url_name': 'orders_list',
            'query': 'status=pending',
            'severity': 'info',
        })
        warehouse_insights = []

    return {
        'role': role,
        'view_type': view_type,
        'base_stats': base,
        'focus_cards': focus_cards,
        'quick_actions': quick_actions,
        'risk_entries': risk_entries,
        'kpi_entries': [
            {'key': 'fulfillment_rate', 'label': '履约率', 'value': f"{base.get('fulfillment_rate', 0.0)}%", 'url_name': 'orders_list', 'query': 'status=completed'},
            {'key': 'cancel_rate', 'label': '取消率', 'value': f"{base.get('cancel_rate', 0.0)}%", 'url_name': 'orders_list', 'query': 'status=cancelled'},
            {'key': 'avg_transit_days', 'label': '平均在途天数', 'value': f"{base.get('avg_transit_days', 0.0)} 天", 'url_name': 'orders_list', 'query': 'status=completed'},
        ],
        'risk_hints': {
            'pending_transfer_tasks': pending_transfer_tasks,
            'overdue_pending_transfers': overdue_pending_transfers,
            'overdue_pending_approvals': overdue_pending_approvals,
            'low_stock_parts': base['low_stock_parts'],
        },
        'reservation_followup': reservation_followup,
        'reservation_post_convert': reservation_post_convert,
        'reservation_converted_followup': reservation_converted_followup,
        'reservation_owner_panels': reservation_owner_panels,
        'reservation_owner_transfer_suggestions': reservation_owner_transfer_suggestions,
        'warehouse_insights': warehouse_insights,
    }


def run_data_consistency_checks():
    """
    数据一致性巡检（只读）：
    - SKU库存 vs 单套实例总数
    - 转寄锁重复聚合
    - 待执行转寄任务与锁数量匹配
    - 财务对账差异（订单账 vs 交易流水）
    """
    issues = []

    # 1) 兼容字段一致性：SKU.stock 仅作为历史兼容镜像，应同步为该 SKU 激活单套数
    for sku in SKU.objects.filter(is_active=True):
        unit_total = InventoryUnit.objects.filter(sku=sku, is_active=True).count()
        if int(unit_total) != int(sku.stock or 0):
            issues.append({
                'type': 'legacy_stock_mismatch',
                'severity': 'warning',
                'message': f'SKU {sku.code} 的兼容库存字段与单套实例不一致',
                'meta': {
                    'sku_id': sku.id,
                    'sku_code': sku.code,
                    'legacy_stock': int(sku.stock or 0),
                    'unit_total': int(unit_total),
                }
            })

    # 2) 锁重复：同 source->target->sku 若存在多条 locked，提示检查（部分拆分可能出现，先按warning）
    duplicate_locked = (
        TransferAllocation.objects.filter(status='locked')
        .values('source_order_id', 'target_order_id', 'sku_id')
        .annotate(row_count=Count('id'), quantity_total=Sum('quantity'))
        .filter(row_count__gt=1)
    )
    for row in duplicate_locked:
        issues.append({
            'type': 'duplicate_locked_allocations',
            'severity': 'warning',
            'message': '同来源/目标/SKU存在多条已锁定挂靠记录',
            'meta': {
                'source_order_id': row['source_order_id'],
                'target_order_id': row['target_order_id'],
                'sku_id': row['sku_id'],
                'row_count': int(row['row_count'] or 0),
                'quantity_total': int(row['quantity_total'] or 0),
            }
        })

    # 3) 转寄任务与锁数量匹配：pending任务数量不应大于同键 locked 总数
    pending_transfers = Transfer.objects.filter(status='pending').values(
        'order_from_id', 'order_to_id', 'sku_id'
    ).annotate(quantity_total=Sum('quantity'))
    for row in pending_transfers:
        locked_qty = (
            TransferAllocation.objects.filter(
                source_order_id=row['order_from_id'],
                target_order_id=row['order_to_id'],
                sku_id=row['sku_id'],
                status='locked',
            ).aggregate(total=Sum('quantity'))['total'] or 0
        )
        if int(locked_qty) < int(row['quantity_total'] or 0):
            issues.append({
                'type': 'transfer_locked_shortage',
                'severity': 'error',
                'message': '待执行转寄任务数量超过已锁定挂靠数量',
                'meta': {
                    'source_order_id': row['order_from_id'],
                    'target_order_id': row['order_to_id'],
                    'sku_id': row['sku_id'],
                    'pending_qty': int(row['quantity_total'] or 0),
                    'locked_qty': int(locked_qty or 0),
                }
            })

    # 4) 财务对账差异：复用财务对账口径，将异常订单纳入巡检
    reconciliation_rows = build_finance_reconciliation_rows(mismatch_only=True)
    for row in reconciliation_rows:
        order = row.get('order')
        if not order:
            continue
        issues.append({
            'type': 'finance_reconciliation_mismatch',
            'severity': 'warning',
            'message': f'订单 {order.order_no} 财务对账存在差异',
            'meta': {
                'order_id': order.id,
                'order_no': order.order_no,
                'customer_name': order.customer_name,
                'deposit_diff': str(row.get('deposit_diff') or Decimal('0.00')),
                'balance_diff': str(row.get('balance_diff') or Decimal('0.00')),
                'refund_diff': str(row.get('refund_diff') or Decimal('0.00')),
                'mismatch_fields': row.get('mismatch_fields') or [],
                'suggestions': row.get('suggestions') or [],
            }
        })

    type_counts = {}
    for issue in issues:
        t = issue.get('type') or 'unknown'
        type_counts[t] = int(type_counts.get(t, 0)) + 1

    return {
        'total_issues': len(issues),
        'error_count': sum(1 for i in issues if i['severity'] == 'error'),
        'warning_count': sum(1 for i in issues if i['severity'] == 'warning'),
        'type_counts': type_counts,
        'issues': issues,
    }


def persist_data_consistency_check_result(result, executed_by=None, source='manual'):
    """保存一致性巡检结果台账"""
    result = result or {}
    return DataConsistencyCheckRun.objects.create(
        source=source or 'manual',
        total_issues=int(result.get('total_issues') or 0),
        summary={
            'error_count': int(result.get('error_count') or 0),
            'warning_count': int(result.get('warning_count') or 0),
            'type_counts': result.get('type_counts') or {},
        },
        issues=result.get('issues') or [],
        executed_by=executed_by,
    )


def build_data_consistency_repair_plan(result):
    """
    基于巡检结果生成修复计划（仅包含可自动修复与人工建议）。
    当前自动修复项：
    - legacy_stock_mismatch: 将兼容字段 SKU.stock 同步为激活单套数
    """
    result = result or {}
    issues = result.get('issues') or []
    auto_repairs = []
    manual_items = []
    for issue in issues:
        issue_type = issue.get('type')
        meta = issue.get('meta') or {}
        if issue_type == 'legacy_stock_mismatch':
            auto_repairs.append({
                'type': issue_type,
                'sku_id': meta.get('sku_id'),
                'sku_code': meta.get('sku_code'),
                'from_stock': int(meta.get('legacy_stock') or 0),
                'to_stock': int(meta.get('unit_total') or 0),
            })
        elif issue_type == 'duplicate_locked_allocations':
            manual_items.append({
                'type': issue_type,
                'message': '同来源/目标/SKU有多条锁，建议人工核对后释放冗余锁。',
                'meta': meta,
            })
        elif issue_type == 'transfer_locked_shortage':
            manual_items.append({
                'type': issue_type,
                'message': '待执行转寄数量超过锁数量，建议先补锁或取消异常任务。',
                'meta': meta,
            })
        else:
            manual_items.append({
                'type': issue_type or 'unknown',
                'message': issue.get('message') or '未知问题，请人工处理。',
                'meta': meta,
            })
    return {
        'auto_repairs': auto_repairs,
        'manual_items': manual_items,
    }


def check_sku_availability(sku_id, event_date, quantity=1, exclude_order_id=None, rental_days=1):
    """
    检查SKU在指定日期的可用性

    Args:
        sku_id: SKU ID
        event_date: 活动日期
        quantity: 需要的数量
        exclude_order_id: 排除的订单ID（用于编辑订单时）

    Returns:
        dict: {
            'available': bool,  # 是否可用
            'current_stock': int,  # 总库存
            'occupied': int,  # 已占用
            'available_count': int,  # 可用数量
            'message': str  # 提示信息
        }
    """
    try:
        sku = SKU.objects.get(id=sku_id, is_active=True)
    except SKU.DoesNotExist:
        return {
            'available': False,
            'current_stock': 0,
            'occupied': 0,
            'available_count': 0,
            'message': 'SKU不存在或已禁用'
        }

    # 仓库实时可用库存：仅按“当前未回仓”的占用计算，不按预定日期做时间复用
    query = Q(status__in=['pending', 'confirmed', 'delivered', 'in_use'])

    if exclude_order_id:
        query &= ~Q(id=exclude_order_id)

    active_orders = Order.objects.filter(query)
    # 仓库占用 = 订单明细数量 - 已锁定/已消耗转寄数量
    occupied_raw = OrderItem.objects.filter(
        order__in=active_orders,
        sku_id=sku_id
    ).aggregate(total=Sum('quantity'))['total'] or 0
    transfer_allocated = TransferAllocation.objects.filter(
        target_order__in=active_orders,
        source_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
        sku_id=sku_id,
        status__in=['locked', 'consumed']
    ).aggregate(total=Sum('quantity'))['total'] or 0
    occupied = max(occupied_raw - transfer_allocated, 0)

    effective_stock = sku.effective_stock
    raw_available_count = effective_stock - occupied
    available_count = max(raw_available_count, 0)
    overbooked_count = max(-raw_available_count, 0)

    return {
        'available': raw_available_count >= quantity,
        'current_stock': effective_stock,
        'occupied': occupied,
        'available_count': available_count,
        'overbooked_count': overbooked_count,
        'message': (
            f'仓库可用：{available_count}/{effective_stock}（占用：{occupied}）'
            if raw_available_count >= quantity
            else (
                f'仓库库存不足，仅剩{available_count}套（占用：{occupied}）'
                if overbooked_count == 0
                else f'仓库库存不足，当前超占{overbooked_count}套（占用：{occupied}）'
            )
        )
    }


def get_reservation_conflict_summary(sku_id, event_date, quantity=1, exclude_reservation_id=None):
    """预定单档期提醒：给出同款同日预定、正式订单占用与仓库实时可用量。"""
    if not sku_id or not event_date:
        return None

    try:
        sku = SKU.objects.get(id=sku_id, is_active=True)
    except SKU.DoesNotExist:
        return None

    reservation_query = Reservation.objects.filter(
        sku_id=sku_id,
        event_date=event_date,
        status__in=['pending_info', 'ready_to_convert'],
    )
    if exclude_reservation_id:
        reservation_query = reservation_query.exclude(id=exclude_reservation_id)
    reservation_count = reservation_query.count()
    reservation_quantity = reservation_query.aggregate(total=Sum('quantity'))['total'] or 0
    reservation_samples = list(
        reservation_query.order_by('-created_at').values_list('reservation_no', flat=True)[:3]
    )

    order_quantity = (
        OrderItem.objects.filter(
            sku_id=sku_id,
            order__event_date=event_date,
        )
        .exclude(order__status='cancelled')
        .aggregate(total=Sum('quantity'))['total'] or 0
    )
    order_count = (
        Order.objects.filter(
            items__sku_id=sku_id,
            event_date=event_date,
        )
        .exclude(status='cancelled')
        .distinct()
        .count()
    )

    availability = check_sku_availability(sku_id, event_date, quantity=quantity)
    demand_total = int(reservation_quantity or 0) + int(order_quantity or 0) + int(quantity or 0)
    current_stock = int(availability.get('current_stock') or 0)
    available_count = int(availability.get('available_count') or 0)
    risk_level = 'safe'
    if demand_total > current_stock:
        risk_level = 'danger'
    elif demand_total > available_count:
        risk_level = 'warning'

    return {
        'sku_id': sku_id,
        'sku_code': sku.code,
        'sku_name': sku.name,
        'event_date': event_date,
        'requested_quantity': int(quantity or 0),
        'same_day_reservation_count': reservation_count,
        'same_day_reservation_quantity': int(reservation_quantity or 0),
        'same_day_order_count': order_count,
        'same_day_order_quantity': int(order_quantity or 0),
        'reservation_samples': reservation_samples,
        'current_stock': current_stock,
        'available_count': available_count,
        'occupied': int(availability.get('occupied') or 0),
        'risk_level': risk_level,
        'message': (
            f'同款同日已有 {reservation_count} 张预定单（{int(reservation_quantity or 0)} 套），'
            f'{order_count} 张正式订单（{int(order_quantity or 0)} 套）；'
            f'当前仓库实时可用 {available_count}/{current_stock} 套。'
        ),
    }


CITY_COORDS = {
    '北京市': (39.9042, 116.4074),
    '上海市': (31.2304, 121.4737),
    '天津市': (39.0842, 117.2009),
    '重庆市': (29.5630, 106.5516),
    '石家庄市': (38.0428, 114.5149),
    '太原市': (37.8706, 112.5489),
    '呼和浩特市': (40.8426, 111.7492),
    '沈阳市': (41.8057, 123.4315),
    '长春市': (43.8171, 125.3235),
    '哈尔滨市': (45.8038, 126.5349),
    '南京市': (32.0603, 118.7969),
    '杭州市': (30.2741, 120.1551),
    '合肥市': (31.8206, 117.2290),
    '福州市': (26.0745, 119.2965),
    '厦门市': (24.4798, 118.0894),
    '莆田市': (25.4541, 119.0076),
    '三明市': (26.2638, 117.6392),
    '漳州市': (24.5130, 117.6618),
    '南平市': (26.6419, 118.1785),
    '龙岩市': (25.0751, 117.0174),
    '宁德市': (26.6657, 119.5479),
    '泉州市': (24.8741, 118.6759),
    '南昌市': (28.6820, 115.8579),
    '济南市': (36.6512, 117.1201),
    '郑州市': (34.7466, 113.6254),
    '武汉市': (30.5928, 114.3055),
    '长沙市': (28.2282, 112.9388),
    '广州市': (23.1291, 113.2644),
    '珠海市': (22.2710, 113.5767),
    '汕头市': (23.3535, 116.6822),
    '佛山市': (23.0215, 113.1214),
    '江门市': (22.5787, 113.0819),
    '湛江市': (21.2707, 110.3594),
    '茂名市': (21.6633, 110.9252),
    '肇庆市': (23.0469, 112.4651),
    '惠州市': (23.1115, 114.4168),
    '梅州市': (24.2991, 116.1176),
    '汕尾市': (22.7862, 115.3751),
    '河源市': (23.7463, 114.6978),
    '阳江市': (21.8583, 111.9822),
    '清远市': (23.6820, 113.0560),
    '东莞市': (23.0207, 113.7518),
    '中山市': (22.5176, 113.3928),
    '潮州市': (23.6567, 116.6226),
    '韶关市': (24.8104, 113.5972),
    '深圳市': (22.5431, 114.0579),
    '揭阳市': (23.5497, 116.3728),
    '云浮市': (22.9152, 112.0445),
    '南宁市': (22.8170, 108.3669),
    '海口市': (20.0442, 110.1999),
    '成都市': (30.5728, 104.0668),
    '贵阳市': (26.6470, 106.6302),
    '昆明市': (25.0389, 102.7183),
    '拉萨市': (29.6525, 91.1721),
    '西安市': (34.3416, 108.9398),
    '咸阳市': (34.3296, 108.7093),
    '铜川市': (34.8967, 108.9451),
    '宝鸡市': (34.3619, 107.2373),
    '渭南市': (34.4994, 109.5102),
    '延安市': (36.5853, 109.4897),
    '汉中市': (33.0676, 107.0238),
    '榆林市': (38.2852, 109.7341),
    '安康市': (32.6847, 109.0293),
    '商洛市': (33.8739, 109.9186),
    '兰州市': (36.0611, 103.8343),
    '西宁市': (36.6171, 101.7782),
    '银川市': (38.4872, 106.2309),
    '乌鲁木齐市': (43.8256, 87.6168),
    '香港特别行政区': (22.3193, 114.1694),
    '澳门特别行政区': (22.1987, 113.5439),
    '台北市': (25.0330, 121.5654),
}

PROVINCE_TO_CAPITAL = {
    '北京市': '北京市',
    '上海市': '上海市',
    '天津市': '天津市',
    '重庆市': '重庆市',
    '河北省': '石家庄市',
    '山西省': '太原市',
    '内蒙古自治区': '呼和浩特市',
    '辽宁省': '沈阳市',
    '吉林省': '长春市',
    '黑龙江省': '哈尔滨市',
    '江苏省': '南京市',
    '浙江省': '杭州市',
    '安徽省': '合肥市',
    '福建省': '福州市',
    '江西省': '南昌市',
    '山东省': '济南市',
    '河南省': '郑州市',
    '湖北省': '武汉市',
    '湖南省': '长沙市',
    '广东省': '广州市',
    '广西壮族自治区': '南宁市',
    '海南省': '海口市',
    '四川省': '成都市',
    '贵州省': '贵阳市',
    '云南省': '昆明市',
    '西藏自治区': '拉萨市',
    '陕西省': '西安市',
    '甘肃省': '兰州市',
    '青海省': '西宁市',
    '宁夏回族自治区': '银川市',
    '新疆维吾尔自治区': '乌鲁木齐市',
    '香港特别行政区': '香港特别行政区',
    '澳门特别行政区': '澳门特别行政区',
    '台湾省': '台北市',
}

CITY_TO_PROVINCE = {
    v: k for k, v in PROVINCE_TO_CAPITAL.items()
}
CITY_TO_PROVINCE.update({
    '深圳市': '广东省',
    '珠海市': '广东省',
    '汕头市': '广东省',
    '佛山市': '广东省',
    '江门市': '广东省',
    '湛江市': '广东省',
    '茂名市': '广东省',
    '肇庆市': '广东省',
    '惠州市': '广东省',
    '梅州市': '广东省',
    '汕尾市': '广东省',
    '河源市': '广东省',
    '阳江市': '广东省',
    '清远市': '广东省',
    '东莞市': '广东省',
    '中山市': '广东省',
    '潮州市': '广东省',
    '韶关市': '广东省',
    '揭阳市': '广东省',
    '云浮市': '广东省',
    '厦门市': '福建省',
    '莆田市': '福建省',
    '三明市': '福建省',
    '漳州市': '福建省',
    '南平市': '福建省',
    '龙岩市': '福建省',
    '宁德市': '福建省',
    '泉州市': '福建省',
    '咸阳市': '陕西省',
    '铜川市': '陕西省',
    '宝鸡市': '陕西省',
    '渭南市': '陕西省',
    '延安市': '陕西省',
    '汉中市': '陕西省',
    '榆林市': '陕西省',
    '安康市': '陕西省',
    '商洛市': '陕西省',
})

CITY_ALIASES = {
    '北京': '北京市',
    '上海': '上海市',
    '天津': '天津市',
    '重庆': '重庆市',
    '广州': '广州市',
    '珠海': '珠海市',
    '汕头': '汕头市',
    '佛山': '佛山市',
    '江门': '江门市',
    '湛江': '湛江市',
    '茂名': '茂名市',
    '肇庆': '肇庆市',
    '惠州': '惠州市',
    '梅州': '梅州市',
    '汕尾': '汕尾市',
    '河源': '河源市',
    '阳江': '阳江市',
    '清远': '清远市',
    '东莞': '东莞市',
    '中山': '中山市',
    '潮州': '潮州市',
    '深圳': '深圳市',
    '韶关': '韶关市',
    '揭阳': '揭阳市',
    '云浮': '云浮市',
    '福州': '福州市',
    '厦门': '厦门市',
    '莆田': '莆田市',
    '三明': '三明市',
    '漳州': '漳州市',
    '南平': '南平市',
    '龙岩': '龙岩市',
    '宁德': '宁德市',
    '泉州': '泉州市',
    '杭州': '杭州市',
    '南京': '南京市',
    '武汉': '武汉市',
    '长沙': '长沙市',
    '成都': '成都市',
    '西安': '西安市',
    '咸阳': '咸阳市',
    '铜川': '铜川市',
    '宝鸡': '宝鸡市',
    '渭南': '渭南市',
    '延安': '延安市',
    '汉中': '汉中市',
    '榆林': '榆林市',
    '安康': '安康市',
    '商洛': '商洛市',
}

PROVINCE_ALIASES = {
    '北京': '北京市',
    '上海': '上海市',
    '天津': '天津市',
    '重庆': '重庆市',
    '河北': '河北省',
    '山西': '山西省',
    '内蒙古': '内蒙古自治区',
    '辽宁': '辽宁省',
    '吉林': '吉林省',
    '黑龙江': '黑龙江省',
    '江苏': '江苏省',
    '浙江': '浙江省',
    '安徽': '安徽省',
    '福建': '福建省',
    '江西': '江西省',
    '山东': '山东省',
    '河南': '河南省',
    '湖北': '湖北省',
    '湖南': '湖南省',
    '广东': '广东省',
    '广西': '广西壮族自治区',
    '海南': '海南省',
    '四川': '四川省',
    '贵州': '贵州省',
    '云南': '云南省',
    '西藏': '西藏自治区',
    '陕西': '陕西省',
    '甘肃': '甘肃省',
    '青海': '青海省',
    '宁夏': '宁夏回族自治区',
    '新疆': '新疆维吾尔自治区',
    '香港': '香港特别行政区',
    '澳门': '澳门特别行政区',
    '台湾': '台湾省',
}

# 省 -> 地级市（含自治州/地区/盟）名称库（用于本地地址解析，不依赖外部API）
PROVINCE_CITY_NAMES = {
    '北京市': ['北京市'],
    '上海市': ['上海市'],
    '天津市': ['天津市'],
    '重庆市': ['重庆市'],
    '河北省': ['石家庄市', '唐山市', '秦皇岛市', '邯郸市', '邢台市', '保定市', '张家口市', '承德市', '沧州市', '廊坊市', '衡水市'],
    '山西省': ['太原市', '大同市', '阳泉市', '长治市', '晋城市', '朔州市', '晋中市', '运城市', '忻州市', '临汾市', '吕梁市'],
    '内蒙古自治区': ['呼和浩特市', '包头市', '乌海市', '赤峰市', '通辽市', '鄂尔多斯市', '呼伦贝尔市', '巴彦淖尔市', '乌兰察布市', '兴安盟', '锡林郭勒盟', '阿拉善盟'],
    '辽宁省': ['沈阳市', '大连市', '鞍山市', '抚顺市', '本溪市', '丹东市', '锦州市', '营口市', '阜新市', '辽阳市', '盘锦市', '铁岭市', '朝阳市', '葫芦岛市'],
    '吉林省': ['长春市', '吉林市', '四平市', '辽源市', '通化市', '白山市', '松原市', '白城市', '延边朝鲜族自治州'],
    '黑龙江省': ['哈尔滨市', '齐齐哈尔市', '鸡西市', '鹤岗市', '双鸭山市', '大庆市', '伊春市', '佳木斯市', '七台河市', '牡丹江市', '黑河市', '绥化市', '大兴安岭地区'],
    '江苏省': ['南京市', '无锡市', '徐州市', '常州市', '苏州市', '南通市', '连云港市', '淮安市', '盐城市', '扬州市', '镇江市', '泰州市', '宿迁市'],
    '浙江省': ['杭州市', '宁波市', '温州市', '嘉兴市', '湖州市', '绍兴市', '金华市', '衢州市', '舟山市', '台州市', '丽水市'],
    '安徽省': ['合肥市', '芜湖市', '蚌埠市', '淮南市', '马鞍山市', '淮北市', '铜陵市', '安庆市', '黄山市', '滁州市', '阜阳市', '宿州市', '六安市', '亳州市', '池州市', '宣城市'],
    '福建省': ['福州市', '厦门市', '莆田市', '三明市', '泉州市', '漳州市', '南平市', '龙岩市', '宁德市'],
    '江西省': ['南昌市', '景德镇市', '萍乡市', '九江市', '新余市', '鹰潭市', '赣州市', '吉安市', '宜春市', '抚州市', '上饶市'],
    '山东省': ['济南市', '青岛市', '淄博市', '枣庄市', '东营市', '烟台市', '潍坊市', '济宁市', '泰安市', '威海市', '日照市', '临沂市', '德州市', '聊城市', '滨州市', '菏泽市'],
    '河南省': ['郑州市', '开封市', '洛阳市', '平顶山市', '安阳市', '鹤壁市', '新乡市', '焦作市', '濮阳市', '许昌市', '漯河市', '三门峡市', '南阳市', '商丘市', '信阳市', '周口市', '驻马店市', '济源市'],
    '湖北省': ['武汉市', '黄石市', '十堰市', '宜昌市', '襄阳市', '鄂州市', '荆门市', '孝感市', '荆州市', '黄冈市', '咸宁市', '随州市', '恩施土家族苗族自治州'],
    '湖南省': ['长沙市', '株洲市', '湘潭市', '衡阳市', '邵阳市', '岳阳市', '常德市', '张家界市', '益阳市', '郴州市', '永州市', '怀化市', '娄底市', '湘西土家族苗族自治州'],
    '广东省': ['广州市', '深圳市', '珠海市', '汕头市', '佛山市', '韶关市', '湛江市', '肇庆市', '江门市', '茂名市', '惠州市', '梅州市', '汕尾市', '河源市', '阳江市', '清远市', '东莞市', '中山市', '潮州市', '揭阳市', '云浮市'],
    '广西壮族自治区': ['南宁市', '柳州市', '桂林市', '梧州市', '北海市', '防城港市', '钦州市', '贵港市', '玉林市', '百色市', '贺州市', '河池市', '来宾市', '崇左市'],
    '海南省': ['海口市', '三亚市', '三沙市', '儋州市'],
    '四川省': ['成都市', '自贡市', '攀枝花市', '泸州市', '德阳市', '绵阳市', '广元市', '遂宁市', '内江市', '乐山市', '南充市', '眉山市', '宜宾市', '广安市', '达州市', '雅安市', '巴中市', '资阳市', '阿坝藏族羌族自治州', '甘孜藏族自治州', '凉山彝族自治州'],
    '贵州省': ['贵阳市', '六盘水市', '遵义市', '安顺市', '毕节市', '铜仁市', '黔西南布依族苗族自治州', '黔东南苗族侗族自治州', '黔南布依族苗族自治州'],
    '云南省': ['昆明市', '曲靖市', '玉溪市', '保山市', '昭通市', '丽江市', '普洱市', '临沧市', '楚雄彝族自治州', '红河哈尼族彝族自治州', '文山壮族苗族自治州', '西双版纳傣族自治州', '大理白族自治州', '德宏傣族景颇族自治州', '怒江傈僳族自治州', '迪庆藏族自治州'],
    '西藏自治区': ['拉萨市', '日喀则市', '昌都市', '林芝市', '山南市', '那曲市', '阿里地区'],
    '陕西省': ['西安市', '铜川市', '宝鸡市', '咸阳市', '渭南市', '延安市', '汉中市', '榆林市', '安康市', '商洛市'],
    '甘肃省': ['兰州市', '嘉峪关市', '金昌市', '白银市', '天水市', '武威市', '张掖市', '平凉市', '酒泉市', '庆阳市', '定西市', '陇南市', '临夏回族自治州', '甘南藏族自治州'],
    '青海省': ['西宁市', '海东市', '海北藏族自治州', '黄南藏族自治州', '海南藏族自治州', '果洛藏族自治州', '玉树藏族自治州', '海西蒙古族藏族自治州'],
    '宁夏回族自治区': ['银川市', '石嘴山市', '吴忠市', '固原市', '中卫市'],
    '新疆维吾尔自治区': ['乌鲁木齐市', '克拉玛依市', '吐鲁番市', '哈密市', '昌吉回族自治州', '博尔塔拉蒙古自治州', '巴音郭楞蒙古自治州', '阿克苏地区', '克孜勒苏柯尔克孜自治州', '喀什地区', '和田地区', '伊犁哈萨克自治州', '塔城地区', '阿勒泰地区'],
    '香港特别行政区': ['香港特别行政区'],
    '澳门特别行政区': ['澳门特别行政区'],
    '台湾省': ['台北市', '新北市', '桃园市', '台中市', '台南市', '高雄市', '基隆市', '新竹市', '嘉义市'],
}


def _city_short_alias(city):
    if not city:
        return None
    for suffix in ['特别行政区', '自治州', '地区', '自治县', '盟', '州', '市']:
        if city.endswith(suffix):
            short = city[:-len(suffix)]
            return short if short else None
    return city


for _province_name, _city_list in PROVINCE_CITY_NAMES.items():
    for _city_name in _city_list:
        CITY_TO_PROVINCE.setdefault(_city_name, _province_name)
        _short = _city_short_alias(_city_name)
        if _short and _short not in CITY_ALIASES:
            CITY_ALIASES[_short] = _city_name


def _normalize_address_text(address):
    text = (address or '').strip()
    if not text:
        return ''
    text = re.sub(r'\s+', '', text)
    text = text.replace('省份', '省').replace('城市', '市')
    return text


def _normalize_city_name(city):
    if not city:
        return None
    city = city.strip()
    if city in CITY_COORDS:
        return city
    if city in CITY_ALIASES:
        return CITY_ALIASES[city]
    if not city.endswith(('市', '州', '地区', '盟')):
        candidate = f'{city}市'
        if candidate in CITY_COORDS:
            return candidate
    return city if city in CITY_COORDS else None


def _normalize_city_display_name(city):
    """用于展示与省市归属判断，不要求必须存在坐标。"""
    if not city:
        return None
    city = city.strip()
    if city in CITY_ALIASES:
        return CITY_ALIASES[city]
    if city.endswith(('市', '州', '地区', '盟')):
        return city
    # 无后缀时按“市”补全用于展示
    return f'{city}市'


def _find_city_from_text(text):
    if not text:
        return None
    for alias, standard in CITY_ALIASES.items():
        if alias in text:
            return standard
    for city in CITY_COORDS.keys():
        if city in text:
            return city
        short = city[:-1] if city.endswith('市') else city
        if short and short in text:
            return city
    return None


def _extract_province_city(address):
    raw = _normalize_address_text(address)
    if not raw:
        return None, None, 'low'
    province_match = re.search(r'(北京市|上海市|天津市|重庆市|[^省]+省|[^区]+自治区|香港特别行政区|澳门特别行政区|台湾省)', raw)
    province = province_match.group(1) if province_match else None
    if not province:
        for alias, full_name in PROVINCE_ALIASES.items():
            if raw.startswith(alias):
                province = full_name
                break
    city_match = re.search(r'([^省市区县]+市|[^省市区县]+自治州|[^省市区县]+州|[^省市区县]+地区|[^省市区县]+盟)', raw)
    city = _normalize_city_display_name(city_match.group(1)) if city_match else None
    inferred_city_from_text = _normalize_city_display_name(_find_city_from_text(raw))
    if city and not _normalize_city_name(city) and inferred_city_from_text:
        # 例如“泉州晋江”被提成“泉州晋江市”时，优先纠偏为文本中可识别城市（泉州市）
        city = inferred_city_from_text
    if not city:
        city = inferred_city_from_text
    if province in ('北京市', '上海市', '天津市', '重庆市') and not city:
        city = province
    # 只有在完全提取不到城市时，才降级到省会
    if not city and province in PROVINCE_TO_CAPITAL:
        city = PROVINCE_TO_CAPITAL[province]
        return province, city, 'low'
    if city and not province:
        province = CITY_TO_PROVINCE.get(city)
        return province, city, 'medium'
    if city and province:
        inferred = CITY_TO_PROVINCE.get(city)
        if inferred and inferred != province:
            province = inferred
            return province, city, 'low'
        return province, city, 'high'
    return province, city, 'low'


def _resolve_city_coord(address):
    province, city, confidence = _extract_province_city(address)
    city_for_coord = _normalize_city_name(city)
    if not city_for_coord:
        inferred = _find_city_from_text(_normalize_address_text(address))
        city_for_coord = _normalize_city_name(inferred)
    if city_for_coord and city_for_coord in CITY_COORDS:
        return CITY_COORDS[city_for_coord], confidence
    if province and province in PROVINCE_TO_CAPITAL:
        capital = PROVINCE_TO_CAPITAL[province]
        coord = CITY_COORDS.get(capital)
        if coord:
            return coord, 'low'
    return None, 'low'


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _address_distance_metrics(source_address, target_address):
    """
    计算地址距离指标。
    返回:
    - score: Decimal, 用于排序（越小越近）
    - mode: 'km' | 'similarity'
    """
    left_coord, _ = _resolve_city_coord(source_address)
    right_coord, _ = _resolve_city_coord(target_address)
    if left_coord and right_coord:
        km = _haversine_km(left_coord[0], left_coord[1], right_coord[0], right_coord[1])
        return Decimal(str(round(km, 4))), 'km'

    left = (source_address or '').strip().lower()
    right = (target_address or '').strip().lower()
    if not left or not right:
        return Decimal('999.0000'), 'similarity'
    # 省市提取失败时，使用字符串相似度兜底:
    # score = (1-ratio)*100，越小表示越像
    ratio = SequenceMatcher(None, left, right).ratio()
    return Decimal(str(round((1 - ratio) * 100, 4))), 'similarity'


def get_transfer_match_candidates(
    delivery_address,
    target_event_date,
    sku_id,
    exclude_target_order_id=None,
):
    """
    获取创建订单时的转寄候选（不分配数量）
    规则：
    1) 来源订单待处理/待发货/已发货；
    2) 同SKU；
    3) 来源预定日期严格早于目标5天（不含5）；
    4) 排序：
       - 主排序：来源预定日期与(目标预定日期+5天)的差值越小越优先
       - 次排序：省市直线距离越近越优先
       - 三排序：来源单号 ASC
    """
    settings = get_system_settings()
    buffer_days = int(settings.get('buffer_days', 5) or 0)
    target_province, target_city, _ = _extract_province_city(delivery_address)
    # 严格 > 5 天：source_date <= target_date - 6
    min_source_date = target_event_date - timedelta(days=6)
    lock_start = target_event_date - timedelta(days=5)
    lock_end = target_event_date + timedelta(days=5)

    source_orders = Order.objects.filter(
        status__in=['pending', 'confirmed', 'delivered'],
        event_date__lte=min_source_date,
        items__sku_id=sku_id
    ).distinct().prefetch_related('items__sku')

    candidates = []
    for order in source_orders:
        source_province, source_city, _ = _extract_province_city(order.delivery_address)
        source_qty = OrderItem.objects.filter(order=order, sku_id=sku_id).aggregate(total=Sum('quantity'))['total'] or 0
        reserved_query = TransferAllocation.objects.filter(
            source_order=order,
            sku_id=sku_id,
            status__in=['locked', 'consumed'],
            target_event_date__gte=lock_start,
            target_event_date__lte=lock_end
        )
        if exclude_target_order_id:
            reserved_query = reserved_query.exclude(target_order_id=exclude_target_order_id)
        reserved_qty = reserved_query.aggregate(total=Sum('quantity'))['total'] or 0
        available_qty = max(source_qty - reserved_qty, 0)
        if available_qty <= 0:
            continue

        distance_score, distance_mode = _address_distance_metrics(order.delivery_address, delivery_address)
        _, source_confidence = _resolve_city_coord(order.delivery_address)
        _, target_confidence = _resolve_city_coord(delivery_address)
        confidence_rank = {'high': 0, 'medium': 1, 'low': 2}
        distance_confidence = source_confidence if confidence_rank[source_confidence] >= confidence_rank[target_confidence] else target_confidence
        target_plus_buffer = target_event_date + timedelta(days=buffer_days)
        date_gap_score = abs((target_plus_buffer - order.event_date).days)
        candidates.append({
            'source_order': order,
            'available_qty': available_qty,
            'date_gap_score': date_gap_score,
            'distance_score': distance_score,
            'distance_mode': distance_mode,
            'distance_confidence': distance_confidence,
            'buffer_days': buffer_days,
            'source_province': source_province,
            'source_city': source_city,
            'target_province': target_province,
            'target_city': target_city,
            'lock_window_start': lock_start,
            'lock_window_end': lock_end,
        })

    confidence_rank = {'high': 0, 'medium': 1, 'low': 2}
    candidates.sort(
        key=lambda item: (
            item['date_gap_score'],
            confidence_rank[item['distance_confidence']],
            item['distance_score'],
            item['source_order'].order_no
        )
    )
    return candidates


def build_transfer_allocation_plan(
    delivery_address,
    target_event_date,
    sku_id,
    quantity,
    preferred_source_order_id=None,
    exclude_target_order_id=None,
):
    """根据候选生成分配方案：优先转寄，不足部分走仓库。"""
    candidates = get_transfer_match_candidates(
        delivery_address,
        target_event_date,
        sku_id,
        exclude_target_order_id=exclude_target_order_id,
    )
    if preferred_source_order_id:
        preferred = [c for c in candidates if c['source_order'].id == preferred_source_order_id]
        others = [c for c in candidates if c['source_order'].id != preferred_source_order_id]
        candidates = preferred + others

    remaining = quantity
    allocations = []
    for c in candidates:
        if remaining <= 0:
            break
        alloc_qty = min(remaining, c['available_qty'])
        if alloc_qty <= 0:
            continue
        allocations.append({
            'source_order_id': c['source_order'].id,
            'source_order_no': c['source_order'].order_no,
            'source_event_date': c['source_order'].event_date,
            'sku_id': sku_id,
            'quantity': alloc_qty,
            'target_event_date': target_event_date,
            'window_start': c['lock_window_start'],
            'window_end': c['lock_window_end'],
            'distance_score': c['distance_score'],
        })
        remaining -= alloc_qty

    return {
        'allocations': allocations,
        'warehouse_needed': max(remaining, 0),
        'candidates': candidates,
    }


def calculate_order_dates(event_date, rental_days=1):
    """
    计算订单的发货日期和回收日期

    Args:
        event_date: 活动日期
        rental_days: 租赁天数

    Returns:
        dict: {
            'ship_date': date,  # 发货日期
            'return_date': date  # 回收日期
        }
    """
    settings = get_system_settings()

    ship_date = event_date - timedelta(days=settings['ship_lead_days'])
    return_date = event_date + timedelta(days=rental_days) + timedelta(days=settings['return_offset_days'])

    return {
        'ship_date': ship_date,
        'return_date': return_date
    }


def find_transfer_candidates():
    """
    查找可转寄的订单对

    Returns:
        list: [
            {
                'order_from': Order,  # 回收订单
                'order_to': Order,  # 发货订单
                'sku': SKU,
                'gap_days': int,  # 间隔天数
                'cost_saved': Decimal  # 节省成本
            }
        ]
    """
    candidates = []
    pending_orders = Order.objects.filter(
        status='pending'
    ).prefetch_related('items__sku')

    for order_to in pending_orders:
        for item_to in order_to.items.all():
            # 该待处理订单明细已挂靠（或已生成任务）则不再进入候选池
            if TransferAllocation.objects.filter(
                target_order=order_to,
                sku_id=item_to.sku_id,
                status__in=['locked', 'consumed']
            ).exists():
                continue
            if Transfer.objects.filter(
                order_to=order_to,
                sku_id=item_to.sku_id,
                status='pending'
            ).exists():
                continue

            match_candidates = get_transfer_match_candidates(
                order_to.delivery_address,
                order_to.event_date,
                item_to.sku_id,
                exclude_target_order_id=order_to.id,
            )
            if not match_candidates:
                continue

            # 候选池只展示“最佳候选”（排序第一名），避免组合爆炸
            c = match_candidates[0]
            order_from = c['source_order']
            gap = (order_to.event_date - order_from.event_date).days
            date_gap_score = int(c.get('date_gap_score', 0) or 0)
            if date_gap_score <= 1:
                date_match_label = '高'
            elif date_gap_score <= 3:
                date_match_label = '中'
            else:
                date_match_label = '低'

            distance_mode = c.get('distance_mode', 'similarity')
            distance_score = c.get('distance_score')
            if distance_mode == 'km':
                distance_desc = f"约 {distance_score} km"
            else:
                distance_desc = f"文本差异分 {distance_score}"
            candidates.append({
                'order_from': order_from,
                'order_to': order_to,
                'sku': item_to.sku,
                'available_qty': c.get('available_qty', 0),
                'date_gap_score': date_gap_score,
                'date_match_label': date_match_label,
                'distance_score': distance_score,
                'distance_mode': distance_mode,
                'distance_desc': distance_desc,
                'suggested_ship_date': order_from.event_date + timedelta(days=1),
                'gap_days': gap,
                'cost_saved': Decimal('100.00'),
            })

    for idx, item in enumerate(candidates, start=1):
        item['priority_text'] = f"P{idx}"
    candidates.sort(key=lambda x: (x['order_to'].event_date, x['gap_days'], x['order_to'].order_no))
    for idx, item in enumerate(candidates, start=1):
        item['priority_text'] = f"P{idx}"
    return candidates


def build_transfer_pool_rows():
    """
    构建转寄中心候选池：
    - 范围：未发货订单（pending/confirmed）的每条订单明细
    - 展示：当前挂靠、推荐来源、是否可重新推荐
    """
    settings = get_system_settings()
    ship_lead_days = int(settings.get('ship_lead_days', 2) or 0)
    warehouse_sender = {
        'name': settings.get('warehouse_sender_name', '仓库发货员'),
        'phone': settings.get('warehouse_sender_phone', '-'),
        'address': settings.get('warehouse_sender_address', '仓库地址未配置'),
    }

    def _distance_desc(from_address, to_address):
        score, mode = _address_distance_metrics(from_address, to_address)
        if mode == 'km':
            return f"约 {score} km"
        return f"文本差异分 {score}"

    def _preview_units(order_id, sku_id, qty=1):
        unit_nos = list(
            InventoryUnit.objects.filter(
                current_order_id=order_id,
                sku_id=sku_id,
                is_active=True,
            ).order_by('unit_no').values_list('unit_no', flat=True)[: max(int(qty or 1), 1) + 1]
        )
        if not unit_nos:
            return '-'
        if len(unit_nos) > int(qty or 1):
            return '、'.join(unit_nos[: int(qty or 1)]) + '...'
        return '、'.join(unit_nos)

    target_orders = Order.objects.filter(
        status__in=['pending', 'confirmed', 'delivered']
    ).prefetch_related('items__sku').order_by('event_date', 'created_at')

    # 当前挂靠展示需要覆盖“已发货”订单，
    # 因为目标单发货后分配锁会从 locked -> consumed，仍应显示真实挂靠来源。
    allocations = TransferAllocation.objects.filter(
        target_order__status__in=['pending', 'confirmed', 'delivered'],
        status__in=['locked', 'consumed']
    ).select_related('source_order')
    alloc_map = {}
    for alloc in allocations:
        alloc_map.setdefault((alloc.target_order_id, alloc.sku_id), []).append(alloc)

    latest_task_map = {}
    blocking_task_map = {}
    for order_to_id, sku_id, status in Transfer.objects.filter(
        order_to__status__in=['pending', 'confirmed', 'delivered'],
        status__in=['pending', 'completed', 'cancelled']
    ).order_by('order_to_id', 'sku_id', '-id').values_list('order_to_id', 'sku_id', 'status'):
        key = (order_to_id, sku_id)
        latest_task_map.setdefault(key, status)
        if status in ['pending', 'completed']:
            blocking_task_map.setdefault(key, status)

    rows = []
    for order in target_orders:
        for item in order.items.all():
            key = (order.id, item.sku_id)
            allocs = alloc_map.get(key, [])
            task_status = latest_task_map.get(key)
            blocking_task_status = blocking_task_map.get(key)
            has_pending_task = blocking_task_status in ['pending', 'completed']
            recommended = get_transfer_match_candidates(
                order.delivery_address,
                order.event_date,
                item.sku_id,
                exclude_target_order_id=order.id,
            )
            top = recommended[0] if recommended else None

            if allocs:
                source_order = allocs[0].source_order
                current_source_text = f"{source_order.order_no}（{sum(a.quantity for a in allocs)}套）"
                current_sender = {
                    'name': source_order.customer_name,
                    'phone': source_order.customer_phone,
                    'address': source_order.delivery_address,
                }
                current_event_date = source_order.event_date
                current_source_type = 'transfer'
                current_units_preview = _preview_units(source_order.id, item.sku_id, item.quantity)
            else:
                current_source_text = '仓库发货'
                current_sender = warehouse_sender
                current_event_date = None
                current_source_type = 'warehouse'
                current_units_preview = '-'
            current_distance_desc = _distance_desc(current_sender.get('address', ''), order.delivery_address)

            if top:
                rec_source = top['source_order']
                rec_source_text = f"{rec_source.order_no}（可转寄{top['available_qty']}套）"
                rec_sender = {
                    'name': rec_source.customer_name,
                    'phone': rec_source.customer_phone,
                    'address': rec_source.delivery_address,
                }
                rec_ship_date = rec_source.event_date + timedelta(days=1)
                recommended_event_date = rec_source.event_date
                recommended_source_type = 'transfer'
                recommended_units_preview = _preview_units(rec_source.id, item.sku_id, item.quantity)
                if top.get('distance_mode') == 'km':
                    recommended_distance_desc = f"约 {top.get('distance_score')} km"
                else:
                    recommended_distance_desc = f"文本差异分 {top.get('distance_score')}"
            else:
                rec_source_text = '仓库发货（当前无可用转寄来源）'
                rec_sender = warehouse_sender
                rec_ship_date = order.event_date - timedelta(days=ship_lead_days)
                recommended_event_date = None
                recommended_source_type = 'warehouse'
                recommended_units_preview = '-'
                recommended_distance_desc = _distance_desc(rec_sender.get('address', ''), order.delivery_address)

            can_recommend = not has_pending_task
            if can_recommend:
                can_recommend_reason = ''
            elif blocking_task_status == 'completed':
                can_recommend_reason = '已存在已完成转寄任务，不可重推'
            else:
                can_recommend_reason = '已存在转寄任务，不可重推'

            can_generate_task = (
                order.status == 'delivered'
                and recommended_source_type == 'transfer'
                and not has_pending_task
            )
            if can_generate_task:
                can_generate_reason = ''
            elif blocking_task_status == 'pending':
                can_generate_reason = '已存在待执行转寄任务'
            elif blocking_task_status == 'completed':
                can_generate_reason = '已存在已完成转寄任务'
            elif order.status != 'delivered':
                can_generate_reason = '目标订单未发货，暂不可生成任务'
            else:
                can_generate_reason = '当前推荐来源为仓库发货，无法生成转寄任务'

            rows.append({
                'row_key': f'{order.id}:{item.sku_id}',
                'order': order,
                'item': item,
                'current_source_text': current_source_text,
                'current_sender': current_sender,
                'current_event_date': current_event_date,
                'current_source_type': current_source_type,
                'current_distance_desc': current_distance_desc,
                'current_units_preview': current_units_preview,
                'recommended_source_text': rec_source_text,
                'recommended_sender': rec_sender,
                'recommended_event_date': recommended_event_date,
                'recommended_source_type': recommended_source_type,
                'recommended_distance_desc': recommended_distance_desc,
                'recommended_units_preview': recommended_units_preview,
                'recommended_ship_date': rec_ship_date,
                'has_pending_task': has_pending_task,
                'task_status': task_status,
                'can_recommend': can_recommend,
                'can_recommend_reason': can_recommend_reason,
                'can_generate_task': can_generate_task,
                'can_generate_reason': can_generate_reason,
            })

    return rows


def create_transfer_task(order_from_id, order_to_id, sku_id, user):
    """
    创建转寄任务

    Args:
        order_from_id: 回收订单ID
        order_to_id: 发货订单ID
        sku_id: SKU ID
        user: 创建人

    Returns:
        Transfer: 转寄任务对象
    """
    order_from = Order.objects.get(id=order_from_id)
    order_to = Order.objects.get(id=order_to_id)
    sku = SKU.objects.get(id=sku_id)

    transfer = Transfer.objects.filter(
        order_from=order_from,
        order_to=order_to,
        sku=sku,
        status='pending'
    ).first()
    if transfer:
        return transfer

    gap_days = (order_to.event_date - order_from.event_date).days
    cost_saved = Decimal('100.00')
    return Transfer.objects.create(
        order_from=order_from,
        order_to=order_to,
        sku=sku,
        quantity=1,
        gap_days=gap_days,
        cost_saved=cost_saved,
        status='pending',
        created_by=user
    )


def sync_transfer_tasks_for_target_order(target_order, user=None, sku_id=None):
    """
    根据目标订单当前的转寄挂靠锁，同步转寄任务。
    - 有挂靠则创建/更新 pending 任务
    - 挂靠移除则自动取消多余 pending 任务
    """
    allocations_qs = TransferAllocation.objects.filter(
        target_order=target_order,
        status='locked'
    )
    if sku_id is not None:
        allocations_qs = allocations_qs.filter(sku_id=sku_id)
    allocations = allocations_qs.values('source_order_id', 'sku_id').annotate(total_qty=Sum('quantity'))

    desired = {}
    for row in allocations:
        key = (row['source_order_id'], row['sku_id'])
        desired[key] = int(row['total_qty'] or 0)

    existing_pending = Transfer.objects.filter(order_to=target_order, status='pending')
    if sku_id is not None:
        existing_pending = existing_pending.filter(sku_id=sku_id)
    existing_pending = existing_pending.select_related('order_from', 'sku')
    existing_map = {(t.order_from_id, t.sku_id): t for t in existing_pending}

    for key, qty in desired.items():
        if qty <= 0:
            continue
        source_order_id, sku_id = key
        transfer = existing_map.get(key)
        source_order = Order.objects.get(id=source_order_id)
        gap_days = (target_order.event_date - source_order.event_date).days
        cost_saved = Decimal('100.00') * Decimal(qty)
        if transfer:
            updates = []
            if transfer.quantity != qty:
                transfer.quantity = qty
                updates.append('quantity')
            if transfer.gap_days != gap_days:
                transfer.gap_days = gap_days
                updates.append('gap_days')
            if transfer.cost_saved != cost_saved:
                transfer.cost_saved = cost_saved
                updates.append('cost_saved')
            if updates:
                transfer.save(update_fields=updates + ['updated_at'])
            continue
        Transfer.objects.create(
            order_from_id=source_order_id,
            order_to=target_order,
            sku_id=sku_id,
            quantity=qty,
            gap_days=gap_days,
            cost_saved=cost_saved,
            status='pending',
            created_by=user
        )

    for key, transfer in existing_map.items():
        if key in desired:
            continue
        transfer.status = 'cancelled'
        transfer.notes = ((transfer.notes + '\n') if transfer.notes else '') + '自动取消：挂靠已移除'
        transfer.save(update_fields=['status', 'notes', 'updated_at'])


def get_calendar_data(year, month):
    """
    获取排期看板数据（月度视图）

    Args:
        year: 年份
        month: 月份

    Returns:
        dict: {
            'dates': [date1, date2, ...],  # 日期列表
            'skus': [sku1, sku2, ...],  # SKU列表
            'data': {
                sku_id: {
                    date: {
                        'occupied': int,  # 占用数量
                        'available': int,  # 可用数量
                        'total': int,  # 总库存
                        'status': str,  # 'full'/'tight'/'ok'
                        'orders': [order1, order2, ...]  # 该日订单列表
                    }
                }
            }
        }
    """
    from datetime import date
    import calendar

    # 生成该月所有日期
    num_days = calendar.monthrange(year, month)[1]
    dates = [date(year, month, day) for day in range(1, num_days + 1)]

    # 获取所有启用的SKU
    skus = SKU.objects.filter(is_active=True)

    data = {}

    for sku in skus:
        data[sku.id] = {}

        for d in dates:
            # 查询该日期的订单
            orders = Order.objects.filter(
                status__in=['pending', 'confirmed', 'delivered', 'in_use'],
                items__sku=sku
            ).distinct()

            # 统计占用数量
            occupied = OrderItem.objects.filter(
                order__in=orders,
                sku=sku
            ).aggregate(total=Sum('quantity'))['total'] or 0

            total_stock = sku.effective_stock
            available = total_stock - occupied

            # 判断状态
            if available == 0:
                status = 'full'
            elif available <= total_stock * 0.2:
                status = 'tight'
            else:
                status = 'ok'

            data[sku.id][d] = {
                'occupied': occupied,
                'available': available,
                'total': total_stock,
                'status': status,
                'orders': list(orders)
            }

    return {
        'dates': dates,
        'skus': list(skus),
        'data': data
    }


def get_low_stock_parts():
    """
    获取库存不足的部件列表

    Returns:
        QuerySet: 库存不足的部件
    """
    from .models import Part
    from django.db.models import F

    return Part.objects.filter(
        is_active=True,
        current_stock__lt=F('safety_stock')
    ).order_by('current_stock')


def calculate_order_amount(order_items):
    """
    计算订单金额

    Args:
        order_items: 订单明细列表 [{'sku_id': 1, 'quantity': 2}, ...]

    Returns:
        dict: {
            'total_amount': Decimal,  # 总金额
            'total_deposit': Decimal,  # 总押金
            'total_rental': Decimal  # 总租金
        }
    """
    total_deposit = Decimal('0.00')
    total_rental = Decimal('0.00')

    for item in order_items:
        sku = SKU.objects.get(id=item['sku_id'])
        quantity = item['quantity']

        total_deposit += sku.deposit * quantity
        total_rental += sku.rental_price * quantity

    # 订单总额仅统计租金，押金单独管理
    total_amount = total_rental

    return {
        'total_amount': total_amount,
        'total_deposit': total_deposit,
        'total_rental': total_rental
    }
