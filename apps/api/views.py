"""API视图模块 - RESTful API接口（真实数据库）"""
from decimal import Decimal
from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.db.models import Q, Count
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.models import (
    Order, SKU, Part, PurchaseOrder, Transfer, TransferAllocation, FinanceTransaction,
    ApprovalTask, RiskEvent, DataConsistencyCheckRun, TransferRecommendationLog,
)
from apps.core.permissions import filter_queryset_by_permission, has_action_permission, has_permission
from apps.core.services import OrderService, ProcurementService, AuditService
from apps.core.utils import (
    create_transfer_task,
    get_dashboard_stats_payload,
    get_role_dashboard_payload,
    build_finance_reconciliation_rows,
)
from .serializers import (
    OrderSerializer, SKUSerializer, PartSerializer, PurchaseOrderSerializer,
    TransferSerializer, FinanceTransactionSerializer, RiskEventSerializer, ApprovalTaskSerializer,
    TransferRecommendationLogSerializer,
)


def _is_transfer_source_order_active(order):
    return TransferAllocation.objects.filter(
        source_order=order,
        status__in=['locked', 'consumed'],
        target_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
    ).exists()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_orders_list(request):
    """获取订单列表"""
    queryset = Order.objects.select_related('created_by').prefetch_related('items__sku').order_by('-created_at')
    queryset = filter_queryset_by_permission(queryset, request.user, 'Order')

    status_filter = request.GET.get('status')
    if status_filter:
        queryset = queryset.filter(status=status_filter)

    serializer = OrderSerializer(queryset, many=True)
    return Response({
        'success': True,
        'data': serializer.data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_order_finance_transactions(request, order_id):
    """获取订单资金流水"""
    order = get_object_or_404(Order, id=order_id)
    queryset = FinanceTransaction.objects.filter(order=order).select_related('created_by').order_by('-created_at', '-id')
    serializer = FinanceTransactionSerializer(queryset, many=True)
    return Response({
        'success': True,
        'data': serializer.data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_finance_reconciliation(request):
    """财务对账中心API（订单维度）"""
    if not has_permission(request.user, 'finance', 'view'):
        return Response({'success': False, 'message': '没有权限（需要：finance - view）'}, status=403)

    status_filter = (request.GET.get('status') or '').strip()
    keyword = (request.GET.get('keyword') or '').strip()
    mismatch_only = (request.GET.get('mismatch_only') or '').strip() == '1'
    mismatch_field = (request.GET.get('mismatch_field') or '').strip()
    min_diff_amount_raw = (request.GET.get('min_diff_amount') or '').strip()
    try:
        min_diff_amount = Decimal(min_diff_amount_raw or '0')
    except Exception:
        min_diff_amount = Decimal('0')
    if min_diff_amount < Decimal('0'):
        min_diff_amount = Decimal('0')

    rows_raw = build_finance_reconciliation_rows(
        status_filter=status_filter,
        keyword=keyword,
        mismatch_only=mismatch_only,
        mismatch_field=mismatch_field,
        min_diff_amount=min_diff_amount,
    )
    rows = []
    for r in rows_raw:
        order = r['order']
        rows.append({
            'order_id': order.id,
            'order_no': order.order_no,
            'customer_name': order.customer_name,
            'status': order.status,
            'status_display': order.get_status_display(),
            'expected_deposit': str(r['expected_deposit']),
            'tx_deposit_received': str(r['tx_deposit_received']),
            'deposit_diff': str(r['deposit_diff']),
            'expected_balance_received': str(r['expected_balance_received']),
            'tx_balance_received': str(r['tx_balance_received']),
            'balance_diff': str(r['balance_diff']),
            'expected_refund': str(r['expected_refund']),
            'tx_deposit_refund': str(r['tx_deposit_refund']),
            'refund_diff': str(r['refund_diff']),
            'tx_penalty': str(r['tx_penalty']),
            'has_mismatch': r['has_mismatch'],
            'suggestions': r['suggestions'],
            'created_at': order.created_at.isoformat() if order.created_at else '',
        })
    mismatch_stats = {
        'total': len(rows_raw),
        'abnormal': sum(1 for r in rows_raw if r.get('has_mismatch')),
        'deposit_count': sum(1 for r in rows_raw if 'deposit' in (r.get('mismatch_fields') or [])),
        'balance_count': sum(1 for r in rows_raw if 'balance' in (r.get('mismatch_fields') or [])),
        'refund_count': sum(1 for r in rows_raw if 'refund' in (r.get('mismatch_fields') or [])),
        'max_abs_diff': str(max(
            [
                max(
                    abs(r.get('deposit_diff') or Decimal('0.00')),
                    abs(r.get('balance_diff') or Decimal('0.00')),
                    abs(r.get('refund_diff') or Decimal('0.00')),
                )
                for r in rows_raw
            ] + [Decimal('0.00')]
        )),
    }

    return Response({
        'success': True,
        'data': rows,
        'meta': {
            'total': len(rows),
            'mismatch_count': sum(1 for r in rows if r['has_mismatch']),
            'status_filter': status_filter,
            'keyword': keyword,
            'mismatch_only': mismatch_only,
            'mismatch_field': mismatch_field,
            'min_diff_amount': str(min_diff_amount),
            'mismatch_stats': mismatch_stats,
        },
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_skus_list(request):
    """获取SKU列表"""
    queryset = SKU.objects.filter(is_active=True).order_by('code')
    serializer = SKUSerializer(queryset, many=True)
    return Response({
        'success': True,
        'data': serializer.data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_parts_inventory(request):
    """获取部件库存"""
    queryset = Part.objects.filter(is_active=True).order_by('name')
    category = request.GET.get('category')
    if category:
        queryset = queryset.filter(category=category)

    serializer = PartSerializer(queryset, many=True)
    return Response({
        'success': True,
        'data': serializer.data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_dashboard_stats(request):
    """获取工作台统计数据"""
    stats = get_dashboard_stats_payload(include_transfer_available=True)
    return Response({
        'success': True,
        'data': stats
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_dashboard_role_view(request):
    """获取角色看板V1数据（只读聚合）"""
    view_role = ''
    if request.user.role in ['admin', 'manager']:
        view_role = (request.GET.get('view_role') or '').strip()
    payload = get_role_dashboard_payload(request.user, view_role=view_role or None)
    return Response({
        'success': True,
        'data': payload
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_dashboard_kpi_trend(request):
    """工作台KPI趋势（按预定日期聚合）"""
    try:
        days = int(request.GET.get('days') or 14)
    except Exception:
        days = 14
    days = max(1, min(days, 90))
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=days - 1)
    rows = (
        Order.objects.filter(event_date__gte=start_date, event_date__lte=end_date)
        .values('event_date', 'status')
        .annotate(cnt=Count('id'))
    )
    by_day = {}
    for i in range(days):
        d = start_date + timedelta(days=i)
        by_day[d] = {
            'date': d.isoformat(),
            'pending': 0,
            'delivered': 0,
            'completed': 0,
            'cancelled': 0,
        }
    for row in rows:
        d = row.get('event_date')
        status = row.get('status')
        if d not in by_day:
            continue
        if status in ['pending', 'delivered', 'completed', 'cancelled']:
            by_day[d][status] = int(row.get('cnt') or 0)
    data = [by_day[start_date + timedelta(days=i)] for i in range(days)]
    return Response({
        'success': True,
        'data': data,
        'meta': {
            'days': days,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
        },
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_ops_alerts(request):
    """运维中心告警聚合（JSON）"""
    if not has_permission(request.user, 'ops_center', 'view'):
        return Response({'success': False, 'message': '没有权限（需要：ops_center - view）'}, status=403)

    source_filter = (request.GET.get('source') or '').strip()
    severity_filter = (request.GET.get('severity') or '').strip()
    transfer_pending_timeout_hours = int(request.GET.get('transfer_pending_timeout_hours') or 24)
    approval_pending_warn_hours = int(request.GET.get('approval_pending_warn_hours') or 24)
    now = timezone.now()

    transfer_overdue_count = Transfer.objects.filter(
        status='pending',
        created_at__lt=now - timedelta(hours=transfer_pending_timeout_hours),
    ).count()
    approval_overdue_count = ApprovalTask.objects.filter(
        status='pending',
        created_at__lt=now - timedelta(hours=approval_pending_warn_hours),
    ).count()
    open_risk_count = RiskEvent.objects.filter(status='open').count()
    latest_check = DataConsistencyCheckRun.objects.order_by('-created_at').first()
    latest_check_issues = int(latest_check.total_issues) if latest_check else 0
    finance_mismatch_count = 0
    if latest_check and latest_check.issues:
        finance_mismatch_count = sum(
            1 for i in (latest_check.issues or [])
            if (i or {}).get('type') == 'finance_reconciliation_mismatch'
        )

    alerts = []
    if transfer_overdue_count > 0:
        alerts.append({
            'source': 'transfer',
            'severity': 'danger',
            'title': '转寄任务超时',
            'value': transfer_overdue_count,
            'desc': f'待执行超过 {transfer_pending_timeout_hours} 小时',
        })
    if approval_overdue_count > 0:
        alerts.append({
            'source': 'approval',
            'severity': 'danger',
            'title': '审批任务超时',
            'value': approval_overdue_count,
            'desc': f'待审批超过 {approval_pending_warn_hours} 小时',
        })
    if open_risk_count > 0:
        alerts.append({
            'source': 'risk',
            'severity': 'warning',
            'title': '待处理风险事件',
            'value': open_risk_count,
            'desc': '风险事件尚未闭环',
        })
    if latest_check and latest_check_issues > 0:
        alerts.append({
            'source': 'consistency',
            'severity': 'warning',
            'title': '一致性巡检存在问题',
            'value': latest_check_issues,
            'desc': f'最近巡检时间：{latest_check.created_at.strftime("%Y-%m-%d %H:%M")}',
        })
    if finance_mismatch_count > 0:
        alerts.append({
            'source': 'finance',
            'severity': 'warning',
            'title': '财务对账异常',
            'value': finance_mismatch_count,
            'desc': '最近巡检识别到财务差异订单',
        })

    if source_filter:
        alerts = [a for a in alerts if a['source'] == source_filter]
    if severity_filter:
        alerts = [a for a in alerts if a['severity'] == severity_filter]

    return Response({
        'success': True,
        'data': {
            'summary': {
                'transfer_overdue_count': transfer_overdue_count,
                'approval_overdue_count': approval_overdue_count,
                'open_risk_count': open_risk_count,
                'latest_check_issues': latest_check_issues,
                'finance_mismatch_count': finance_mismatch_count,
            },
            'alerts': alerts,
        }
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_risk_events(request):
    """风险事件列表API（支持筛选）"""
    if not has_permission(request.user, 'risk_events', 'view'):
        return Response({'success': False, 'message': '没有权限（需要：risk_events - view）'}, status=403)

    status_filter = (request.GET.get('status') or '').strip()
    level_filter = (request.GET.get('level') or '').strip()
    keyword = (request.GET.get('keyword') or '').strip()
    assignee_filter = (request.GET.get('assignee') or '').strip()
    mine_only = (request.GET.get('mine_only') or '').strip() == '1'

    queryset = RiskEvent.objects.select_related('order', 'transfer', 'assignee', 'detected_by').order_by('-created_at')
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if level_filter:
        queryset = queryset.filter(level=level_filter)
    if assignee_filter:
        queryset = queryset.filter(assignee_id=assignee_filter)
    if mine_only:
        queryset = queryset.filter(assignee=request.user)
    if keyword:
        queryset = queryset.filter(
            Q(title__icontains=keyword) |
            Q(description__icontains=keyword) |
            Q(order__order_no__icontains=keyword)
        )
    data = RiskEventSerializer(queryset, many=True).data
    return Response({
        'success': True,
        'data': data,
        'meta': {
            'total': len(data),
            'open_count': queryset.filter(status='open').count(),
            'processing_count': queryset.filter(status='processing').count(),
            'closed_count': queryset.filter(status='closed').count(),
        }
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_approvals(request):
    """审批任务列表API（支持筛选）"""
    if not has_permission(request.user, 'approvals', 'view'):
        return Response({'success': False, 'message': '没有权限（需要：approvals - view）'}, status=403)

    status_filter = (request.GET.get('status') or '').strip()
    action_filter = (request.GET.get('action_code') or '').strip()
    keyword = (request.GET.get('keyword') or '').strip()
    overdue_only = (request.GET.get('overdue_only') or '').strip() == '1'
    mine_only = (request.GET.get('mine_only') or '').strip() == '1'
    reviewable_only = (request.GET.get('reviewable_only') or '').strip() == '1'

    queryset = ApprovalTask.objects.select_related('requested_by', 'reviewed_by').all()
    if request.user.role == 'warehouse_manager' and not request.user.is_superuser:
        queryset = queryset.filter(requested_by=request.user)
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if action_filter:
        queryset = queryset.filter(action_code=action_filter)
    if keyword:
        queryset = queryset.filter(
            Q(task_no__icontains=keyword) |
            Q(target_label__icontains=keyword) |
            Q(summary__icontains=keyword)
        )
    if mine_only:
        queryset = queryset.filter(requested_by=request.user)
    if reviewable_only:
        queryset = queryset.filter(status='pending').exclude(requested_by=request.user)

    approval_warn_hours = int(request.GET.get('approval_pending_warn_hours') or 24)
    cutoff = timezone.now() - timedelta(hours=approval_warn_hours)
    if overdue_only:
        queryset = queryset.filter(status='pending', created_at__lt=cutoff)

    queryset = queryset.order_by('-created_at')
    data = ApprovalTaskSerializer(queryset, many=True).data
    return Response({
        'success': True,
        'data': data,
        'meta': {
            'total': len(data),
            'pending_count': queryset.filter(status='pending').count(),
            'executed_count': queryset.filter(status='executed').count(),
            'rejected_count': queryset.filter(status='rejected').count(),
            'overdue_pending_count': queryset.filter(status='pending', created_at__lt=cutoff).count(),
        },
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_transfer_recommendation_logs(request):
    """转寄推荐回放API（支持筛选）"""
    if not has_permission(request.user, 'transfers', 'view'):
        return Response({'success': False, 'message': '没有权限（需要：transfers - view）'}, status=403)

    keyword = (request.GET.get('keyword') or '').strip()
    trigger_type = (request.GET.get('trigger_type') or '').strip()
    decision_type = (request.GET.get('decision_type') or '').strip()

    queryset = TransferRecommendationLog.objects.select_related('order', 'sku', 'operator').all()
    if keyword:
        queryset = queryset.filter(
            Q(order__order_no__icontains=keyword) |
            Q(order__customer_name__icontains=keyword) |
            Q(sku__code__icontains=keyword) |
            Q(sku__name__icontains=keyword)
        )
    if trigger_type in ['recommend', 'create', 'manual']:
        queryset = queryset.filter(trigger_type=trigger_type)
    if decision_type == 'transfer':
        queryset = queryset.filter(selected_source_order_id__isnull=False)
    elif decision_type == 'warehouse':
        queryset = queryset.filter(Q(selected_source_order_id__isnull=True) | Q(selected_source_order_id=0))

    queryset = queryset.order_by('-created_at', '-id')
    data = TransferRecommendationLogSerializer(queryset, many=True).data
    return Response({
        'success': True,
        'data': data,
        'meta': {
            'total': len(data),
            'trigger_type': trigger_type,
            'decision_type': decision_type,
            'keyword': keyword,
        },
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_transfer_recommendation_log_detail(request, log_id):
    """转寄推荐回放详情API（单条）"""
    if not has_permission(request.user, 'transfers', 'view'):
        return Response({'success': False, 'message': '没有权限（需要：transfers - view）'}, status=403)
    log = get_object_or_404(
        TransferRecommendationLog.objects.select_related('order', 'sku', 'operator'),
        id=log_id,
    )
    data = TransferRecommendationLogSerializer(log).data
    return Response({'success': True, 'data': data})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_order_confirm(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    deposit_paid = Decimal(str(request.data.get('deposit_paid', '0') or '0'))
    try:
        order = OrderService.confirm_order(order.id, deposit_paid, request.user)
        return Response({'success': True, 'data': OrderSerializer(order).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_order_mark_delivered(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    try:
        if not has_action_permission(request.user, 'order.confirm_delivery'):
            raise ValueError('您没有执行此操作的权限（order.confirm_delivery）')
        order = OrderService.mark_as_delivered(order.id, request.data.get('ship_tracking', ''), request.user)
        return Response({'success': True, 'data': OrderSerializer(order).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_order_mark_returned(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    balance_paid = Decimal(str(request.data.get('balance_paid', '0') or '0'))
    try:
        if not has_action_permission(request.user, 'order.mark_returned'):
            raise ValueError('您没有执行此操作的权限（order.mark_returned）')
        if _is_transfer_source_order_active(order):
            raise ValueError('该订单为转寄链路订单，请前往【转寄中心】完成操作')
        order = OrderService.mark_as_returned(order.id, request.data.get('return_tracking', ''), balance_paid, request.user)
        return Response({'success': True, 'data': OrderSerializer(order).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_order_complete(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    try:
        order = OrderService.complete_order(order.id, request.user)
        return Response({'success': True, 'data': OrderSerializer(order).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_purchase_orders(request):
    queryset = PurchaseOrder.objects.select_related('created_by').prefetch_related('items').order_by('-created_at')
    serializer = PurchaseOrderSerializer(queryset, many=True)
    return Response({'success': True, 'data': serializer.data})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_purchase_order_mark_ordered(request, po_id):
    try:
        po = ProcurementService.mark_as_ordered(po_id, request.user)
        return Response({'success': True, 'data': PurchaseOrderSerializer(po).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_purchase_order_mark_arrived(request, po_id):
    try:
        po = ProcurementService.mark_as_arrived(po_id, request.user)
        return Response({'success': True, 'data': PurchaseOrderSerializer(po).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_purchase_order_mark_stocked(request, po_id):
    try:
        po = ProcurementService.mark_as_stocked(po_id, request.user)
        return Response({'success': True, 'data': PurchaseOrderSerializer(po).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_transfers(request):
    queryset = Transfer.objects.select_related('order_from', 'order_to', 'sku').order_by('-created_at')
    serializer = TransferSerializer(queryset, many=True)
    return Response({'success': True, 'data': serializer.data})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_transfer_create(request):
    try:
        if not has_action_permission(request.user, 'transfer.create_task'):
            raise ValueError('您没有执行此操作的权限（transfer.create_task）')
        order_from_id = int(request.data['order_from_id'])
        order_to_id = int(request.data['order_to_id'])
        sku_id = int(request.data['sku_id'])
        existed = Transfer.objects.filter(
            order_from_id=order_from_id,
            order_to_id=order_to_id,
            sku_id=sku_id,
            status='pending'
        ).exists()
        transfer = create_transfer_task(
            order_from_id,
            order_to_id,
            sku_id,
            request.user
        )
        AuditService.log_with_diff(
            user=request.user,
            action='create',
            module='转寄',
            target=f'任务#{transfer.id}',
            summary='API创建转寄任务' + ('（复用已有待执行任务）' if existed else ''),
            before={},
            after={
                'transfer_id': transfer.id,
                'order_from_id': transfer.order_from_id,
                'order_to_id': transfer.order_to_id,
                'sku_id': transfer.sku_id,
                'quantity': transfer.quantity,
                'status': transfer.status,
            },
            extra={'source': 'api', 'existing_pending_reused': existed},
        )
        return Response({'success': True, 'data': TransferSerializer(transfer).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)
