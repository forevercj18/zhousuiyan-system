"""
核心视图模块 - 处理所有前端页面请求（第二阶段：真实数据）
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.urls import reverse
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum, F, ExpressionWrapper, DecimalField, Case, When, Value, IntegerField
from django.db import models, transaction
from django.utils import timezone
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode
import base64
import csv
import json

from .models import (
    Order, OrderItem, SKU, Part, PurchaseOrder, PurchaseOrderItem, PartsMovement,
    AuditLog, User, SystemSettings, Transfer, TransferAllocation, InventoryUnit, UnitMovement,
    InventoryUnitPart, RiskEvent, ApprovalTask, FinanceTransaction, DataConsistencyCheckRun,
    AssemblyOrder, MaintenanceWorkOrder, UnitDisposalOrder, PartRecoveryInspection, PermissionTemplate, Reservation,
    TransferRecommendationLog, SKUImage, SKUComponent,
)
from .services import (
    OrderService, ProcurementService, PartsService, InventoryUnitService, AuditService,
    RiskEventService, ApprovalService, NotificationService, AssemblyService, MaintenanceService, UnitDisposalService,
    OrderImportService,
)
from .services.storage_service import StorageService
from .permissions import (
    require_permission,
    filter_queryset_by_permission,
    has_action_permission,
    can_request_approval,
    PERMISSION_MODULE_LABELS,
    PERMISSION_ACTION_LABELS,
    ACTION_PERMISSION_LABELS,
    get_user_permission_preview,
)
from .utils import (
    find_transfer_candidates,
    create_transfer_task,
    build_transfer_allocation_plan,
    get_transfer_match_candidates,
    build_transfer_pool_rows,
    sync_transfer_tasks_for_target_order,
    get_system_settings,
    get_dashboard_stats_payload,
    get_role_dashboard_payload,
    get_reservation_conflict_summary,
    build_finance_reconciliation_rows,
    run_data_consistency_checks,
    persist_data_consistency_check_result,
)


def _normalize_text(value):
    return ''.join((value or '').strip().split()).lower()


def _snapshot_user_audit(user_obj):
    return {
        'id': user_obj.id,
        'username': user_obj.username,
        'full_name': user_obj.full_name,
        'role': user_obj.role,
        'role_display': user_obj.get_role_display(),
        'permission_mode': getattr(user_obj, 'permission_mode', 'role'),
        'custom_modules': list(getattr(user_obj, 'custom_modules', []) or []),
        'custom_actions': list(getattr(user_obj, 'custom_actions', []) or []),
        'custom_action_permissions': list(getattr(user_obj, 'custom_action_permissions', []) or []),
        'email': user_obj.email,
        'phone': user_obj.phone,
        'is_active': user_obj.is_active,
    }


def _snapshot_permission_template_audit(template):
    return {
        'id': template.id,
        'name': template.name,
        'base_role': template.base_role,
        'base_role_display': template.get_base_role_display(),
        'description': template.description,
        'modules': list(template.modules or []),
        'actions': list(template.actions or []),
        'action_permissions': list(template.action_permissions or []),
        'is_active': template.is_active,
    }


def _snapshot_reservation_audit(reservation):
    return {
        'id': reservation.id,
        'reservation_no': reservation.reservation_no,
        'customer_wechat': reservation.customer_wechat,
        'customer_name': reservation.customer_name,
        'customer_phone': reservation.customer_phone,
        'city': reservation.city,
        'sku_id': reservation.sku_id,
        'sku_code': reservation.sku.code if reservation.sku_id else '',
        'sku_name': reservation.sku.name if reservation.sku_id else '',
        'quantity': reservation.quantity,
        'event_date': reservation.event_date,
        'deposit_amount': reservation.deposit_amount,
        'status': reservation.status,
        'status_display': reservation.get_status_display(),
        'notes': reservation.notes,
        'converted_order_id': reservation.converted_order_id,
        'converted_order_no': reservation.converted_order.order_no if reservation.converted_order_id else '',
        'owner_id': reservation.owner_id,
        'owner_name': reservation.owner.get_full_name() if reservation.owner_id and reservation.owner.get_full_name() else (reservation.owner.username if reservation.owner_id else ''),
    }


def _parse_reservation_form_payload(request):
    sku_id = (request.POST.get('sku_id') or '').strip()
    if not sku_id:
        raise ValueError('请选择款式')
    event_date_raw = (request.POST.get('event_date') or '').strip()
    if not event_date_raw:
        raise ValueError('请选择预定日期')
    customer_wechat = (request.POST.get('customer_wechat') or '').strip()
    if not customer_wechat:
        raise ValueError('微信号不能为空')
    deposit_amount_raw = (request.POST.get('deposit_amount') or '0').strip()
    try:
        deposit_amount = Decimal(deposit_amount_raw or '0')
    except Exception as exc:
        raise ValueError('订金金额格式不正确') from exc
    if deposit_amount < Decimal('0.00'):
        raise ValueError('订金金额不能小于0')
    quantity_raw = (request.POST.get('quantity') or '1').strip()
    try:
        quantity = int(quantity_raw or '1')
    except Exception as exc:
        raise ValueError('数量格式不正确') from exc
    if quantity <= 0:
        raise ValueError('数量必须大于0')
    status = (request.POST.get('status') or 'pending_info').strip()
    if status not in dict(Reservation.STATUS_CHOICES):
        raise ValueError('预定状态无效')
    owner_raw = (request.POST.get('owner_id') or '').strip()
    return {
        'customer_wechat': customer_wechat,
        'customer_name': (request.POST.get('customer_name') or '').strip(),
        'customer_phone': (request.POST.get('customer_phone') or '').strip(),
        'city': (request.POST.get('city') or '').strip(),
        'sku_id': int(sku_id),
        'quantity': quantity,
        'event_date': datetime.strptime(event_date_raw, '%Y-%m-%d').date(),
        'deposit_amount': deposit_amount,
        'status': status,
        'notes': (request.POST.get('notes') or '').strip(),
        'owner_id': int(owner_raw) if owner_raw.isdigit() else None,
    }


def _create_reservation_finance_transaction(*, reservation, transaction_type, amount, user, notes='', reference_no=''):
    amount_decimal = Decimal(str(amount or '0'))
    if amount_decimal <= 0:
        return None
    return FinanceTransaction.objects.create(
        reservation=reservation,
        transaction_type=transaction_type,
        amount=amount_decimal,
        notes=notes,
        reference_no=reference_no,
        created_by=user,
    )


def _get_reservation_owner_candidates():
    return User.objects.filter(
        is_active=True,
        role__in=['admin', 'manager', 'customer_service'],
    ).order_by('role', 'full_name', 'username')


def _build_reservation_conflict_summary(payload=None, reservation=None):
    if reservation is not None:
        return get_reservation_conflict_summary(
            reservation.sku_id,
            reservation.event_date,
            quantity=reservation.quantity,
            exclude_reservation_id=reservation.id,
        )
    if not payload:
        return None
    sku_id = payload.get('sku_id')
    event_date = payload.get('event_date')
    if not sku_id or not event_date:
        return None
    return get_reservation_conflict_summary(
        sku_id,
        event_date,
        quantity=payload.get('quantity') or 1,
    )


def _validate_permission_lists(custom_modules, custom_actions, custom_action_permissions):
    allowed_modules = set(PERMISSION_MODULE_LABELS.keys())
    allowed_actions = set(PERMISSION_ACTION_LABELS.keys())
    allowed_action_permissions = set(ACTION_PERMISSION_LABELS.keys())

    if any(item not in allowed_modules for item in custom_modules):
        raise ValueError('包含无效的模块权限')
    if any(item not in allowed_actions for item in custom_actions):
        raise ValueError('包含无效的操作权限')
    if any(item not in allowed_action_permissions for item in custom_action_permissions):
        raise ValueError('包含无效的业务动作权限')


def _get_user_permission_form_payload(request):
    permission_mode = (request.POST.get('permission_mode') or 'role').strip()
    custom_modules = request.POST.getlist('custom_modules')
    custom_actions = request.POST.getlist('custom_actions')
    custom_action_permissions = request.POST.getlist('custom_action_permissions')

    if permission_mode not in {'role', 'custom'}:
        raise ValueError('权限模式无效')

    _validate_permission_lists(custom_modules, custom_actions, custom_action_permissions)

    if permission_mode == 'custom':
        if not custom_modules:
            raise ValueError('自定义搭配至少要勾选一个模块')
        if not custom_actions:
            raise ValueError('自定义搭配至少要勾选一个操作权限')
    else:
        custom_modules = []
        custom_actions = []
        custom_action_permissions = []

    return {
        'permission_mode': permission_mode,
        'custom_modules': custom_modules,
        'custom_actions': custom_actions,
        'custom_action_permissions': custom_action_permissions,
    }


def _get_permission_template_payload(request):
    name = (request.POST.get('name') or '').strip()
    base_role = (request.POST.get('base_role') or '').strip()
    description = (request.POST.get('description') or '').strip()
    modules = request.POST.getlist('modules')
    actions = request.POST.getlist('actions')
    action_permissions = request.POST.getlist('action_permissions')

    if not name:
        raise ValueError('模板名称不能为空')
    if base_role not in dict(User.ROLE_CHOICES):
        raise ValueError('基础角色无效')

    _validate_permission_lists(modules, actions, action_permissions)

    if not modules:
        raise ValueError('权限模板至少要勾选一个模块')
    if not actions:
        raise ValueError('权限模板至少要勾选一个操作权限')

    return {
        'name': name,
        'base_role': base_role,
        'description': description,
        'modules': modules,
        'actions': actions,
        'action_permissions': action_permissions,
    }


def _find_duplicate_orders(customer_phone, delivery_address, event_date, sku_ids=None, exclude_order_id=None, limit=10):
    phone = (customer_phone or '').strip()
    normalized_address = _normalize_text(delivery_address)
    if not phone or not normalized_address or not event_date:
        return []

    queryset = Order.objects.filter(
        customer_phone=phone,
        event_date=event_date,
    ).exclude(status='cancelled')
    if exclude_order_id:
        queryset = queryset.exclude(id=exclude_order_id)
    if sku_ids:
        queryset = queryset.filter(items__sku_id__in=sku_ids).distinct()
    queryset = queryset.prefetch_related('items__sku').order_by('-created_at')

    duplicates = []
    for order in queryset:
        if _normalize_text(order.delivery_address) != normalized_address:
            continue
        overlap_sku_ids = set()
        if sku_ids:
            overlap_sku_ids = {item.sku_id for item in order.items.all() if item.sku_id in sku_ids}
            if not overlap_sku_ids:
                continue
        duplicates.append({
            'id': order.id,
            'order_no': order.order_no,
            'customer_name': order.customer_name,
            'customer_phone': order.customer_phone,
            'event_date': order.event_date,
            'status': order.status,
            'created_at': order.created_at,
            'delivery_address': order.delivery_address,
            'overlap_sku_ids': overlap_sku_ids,
        })
        if len(duplicates) >= limit:
            break
    return duplicates


def _build_querystring(request, exclude_keys=None):
    params = request.GET.copy()
    for key in (exclude_keys or []):
        params.pop(key, None)
    return params.urlencode()


def _build_orders_base_queryset(request):
    today = timezone.localdate()
    warning_end = today + timedelta(days=7)
    shipped_condition = (
        Q(status__in=['delivered', 'in_use', 'returned', 'completed']) |
        Q(ship_tracking__isnull=False, ship_tracking__gt='')
    )

    orders = filter_queryset_by_permission(
        Order.objects.select_related('created_by').prefetch_related(
            'transfer_allocations_target__source_order',
            'items__sku',
        ).annotate(
            expected_deposit=Sum(
                ExpressionWrapper(
                    F('items__deposit') * F('items__quantity'),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            ),
            shipping_timeliness_priority=Case(
                When(shipped_condition, then=Value(3)),
                When(ship_date__isnull=True, then=Value(4)),
                When(ship_date__lte=today, then=Value(0)),
                When(ship_date__lte=warning_end, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            ),
        ),
        request.user,
        'Order'
    )

    status_filter = request.GET.get('status', '')
    keyword = request.GET.get('keyword', '')
    sla_filter = (request.GET.get('sla', '') or '').strip()
    source_filter = (request.GET.get('source', '') or '').strip()
    return_service_filter = (request.GET.get('return_service', '') or '').strip()
    return_payment_filter = (request.GET.get('return_payment', '') or '').strip()
    pickup_filter = (request.GET.get('pickup', '') or '').strip()

    if status_filter:
        orders = orders.filter(status=status_filter)

    if keyword:
        orders = orders.filter(
            Q(order_no__icontains=keyword) |
            Q(customer_name__icontains=keyword) |
            Q(customer_phone__icontains=keyword) |
            Q(customer_wechat__icontains=keyword) |
            Q(xianyu_order_no__icontains=keyword) |
            Q(source_order_no__icontains=keyword) |
            Q(return_service_payment_reference__icontains=keyword)
        )

    if source_filter in dict(Order.ORDER_SOURCE_CHOICES):
        orders = orders.filter(order_source=source_filter)
    if return_service_filter in dict(Order.RETURN_SERVICE_TYPE_CHOICES):
        orders = orders.filter(return_service_type=return_service_filter)
    if return_payment_filter in dict(Order.RETURN_SERVICE_PAYMENT_STATUS_CHOICES):
        orders = orders.filter(return_service_payment_status=return_payment_filter)
    if pickup_filter in dict(Order.RETURN_PICKUP_STATUS_CHOICES):
        orders = orders.filter(return_pickup_status=pickup_filter)

    if sla_filter == 'overdue':
        orders = orders.filter(ship_date__isnull=False, ship_date__lte=today).exclude(shipped_condition)
    elif sla_filter == 'warning':
        orders = orders.filter(ship_date__gt=today, ship_date__lte=warning_end).exclude(shipped_condition)
    elif sla_filter == 'normal':
        orders = orders.filter(ship_date__gt=warning_end).exclude(shipped_condition)
    elif sla_filter == 'shipped':
        orders = orders.filter(shipped_condition)
    elif sla_filter == 'unknown':
        orders = orders.filter(ship_date__isnull=True).exclude(shipped_condition)

    return orders.order_by('shipping_timeliness_priority', 'ship_date', '-created_at'), {
        'today': today,
        'status_filter': status_filter,
        'keyword': keyword,
        'sla_filter': sla_filter,
        'source_filter': source_filter,
        'return_service_filter': return_service_filter,
        'return_payment_filter': return_payment_filter,
        'pickup_filter': pickup_filter,
    }


def _attach_orders_list_meta(order_list, today):
    def _timeliness_for_order(order):
        shipped = bool(order.ship_tracking) or order.status in ['delivered', 'in_use', 'returned', 'completed']
        if order.ship_date:
            remaining_days = 0 if (order.ship_date < today and shipped) else (order.ship_date - today).days
        else:
            remaining_days = None
        if shipped:
            return {
                'remaining_days': remaining_days if remaining_days is not None else 0,
                'code': 'shipped',
                'label': '🔵 已发货',
                'badge_class': 'text-bg-primary',
                'priority': 3,
            }
        if remaining_days is None:
            return {
                'remaining_days': None,
                'code': 'unknown',
                'label': '⚪ 待补发货日期',
                'badge_class': 'text-bg-secondary',
                'priority': 4,
            }
        if remaining_days <= 0:
            return {
                'remaining_days': remaining_days,
                'code': 'overdue',
                'label': '🔴 已超时，请尽快发货',
                'badge_class': 'text-bg-danger',
                'priority': 0,
            }
        if remaining_days <= 7:
            return {
                'remaining_days': remaining_days,
                'code': 'warning',
                'label': '🟠 即将超时（7天内）',
                'badge_class': 'text-bg-warning',
                'priority': 1,
            }
        return {
            'remaining_days': remaining_days,
            'code': 'normal',
            'label': '🟢 正常时效',
            'badge_class': 'text-bg-success',
            'priority': 2,
        }

    _attach_transfer_allocations_display(order_list)
    current_ids = [o.id for o in order_list]
    source_blocked_ids = set(
        TransferAllocation.objects.filter(
            source_order_id__in=current_ids,
            status__in=['locked', 'consumed'],
            target_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
        ).values_list('source_order_id', flat=True)
    )
    source_usage_count_map = {
        row['source_order_id']: row['cnt']
        for row in TransferAllocation.objects.filter(
            source_order_id__in=current_ids,
            status__in=['locked', 'consumed'],
            target_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
        ).values('source_order_id').annotate(cnt=Count('id'))
    }
    for order in order_list:
        t = _timeliness_for_order(order)
        order.shipping_remaining_days = t['remaining_days']
        order.shipping_timeliness_code = t['code']
        order.shipping_timeliness_label = t['label']
        order.shipping_timeliness_badge_class = t['badge_class']
        order.shipping_timeliness_priority = t['priority']
        order.order_source_label = dict(Order.ORDER_SOURCE_CHOICES).get(order.order_source, '-')
        order.return_service_type_label = dict(Order.RETURN_SERVICE_TYPE_CHOICES).get(order.return_service_type, '-')
        order.return_service_payment_status_label = dict(Order.RETURN_SERVICE_PAYMENT_STATUS_CHOICES).get(order.return_service_payment_status, '-')
        order.return_pickup_status_label = dict(Order.RETURN_PICKUP_STATUS_CHOICES).get(order.return_pickup_status, '-')
        order.can_mark_returned_in_orders_center = order.id not in source_blocked_ids
        order.active_as_source_count = source_usage_count_map.get(order.id, 0)
        order.expected_deposit = order.expected_deposit or Decimal('0.00')


def _build_order_form_meta():
    return {
        'order_source_choices': Order.ORDER_SOURCE_CHOICES,
        'return_service_type_choices': Order.RETURN_SERVICE_TYPE_CHOICES,
        'return_service_payment_status_choices': Order.RETURN_SERVICE_PAYMENT_STATUS_CHOICES,
        'return_service_payment_channel_choices': Order.RETURN_SERVICE_PAYMENT_CHANNEL_CHOICES,
        'return_pickup_status_choices': Order.RETURN_PICKUP_STATUS_CHOICES,
    }


def _extract_order_form_values(request, fallback=None):
    values = dict(fallback or {})
    for key in [
        'customer_name', 'customer_phone', 'customer_wechat', 'xianyu_order_no',
        'customer_email', 'delivery_address', 'return_address', 'event_date',
        'rental_days', 'notes', 'order_source', 'source_order_no',
        'return_service_type', 'return_service_fee', 'return_service_payment_status',
        'return_service_payment_channel', 'return_service_payment_reference',
        'return_pickup_status',
    ]:
        if key in request.POST:
            values[key] = request.POST.get(key, '')
    return values


def _get_page_size(setting_key=None, default=10, settings=None):
    cfg = settings if settings is not None else get_system_settings()
    base = cfg.get('page_size_default', default)
    raw = cfg.get(setting_key, base) if setting_key else base
    try:
        size = int(raw)
    except (TypeError, ValueError):
        size = int(default)
    return max(1, min(size, 100))


def _build_recent_day_buckets(days=7):
    today = timezone.localdate()
    day_list = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    return {day: 0 for day in day_list}


def _to_local_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return value.date()
        return timezone.localtime(value).date()
    return value


def _count_by_day(queryset, field_name, days=7):
    buckets = _build_recent_day_buckets(days)
    for value in queryset.values_list(field_name, flat=True):
        day = _to_local_date(value)
        if day in buckets:
            buckets[day] += 1
    return [
        {'date': day.strftime('%m-%d'), 'value': count}
        for day, count in buckets.items()
    ]


def _build_distribution(rows):
    total = sum(item['value'] for item in rows) or 0
    result = []
    for item in rows:
        percent = round((item['value'] / total) * 100, 1) if total else 0
        result.append({
            **item,
            'percent': percent,
        })
    return result


def _parse_audit_details(log):
    raw = (log.details or '').strip()
    parsed = {
        'is_structured': False,
        'summary': raw,
        'changed_fields': [],
        'before_pretty': '',
        'after_pretty': '',
        'extra_pretty': '',
        'raw': raw,
    }
    if not raw:
        return parsed
    try:
        payload = json.loads(raw)
    except Exception:
        return parsed
    if not isinstance(payload, dict):
        return parsed

    parsed['is_structured'] = True
    parsed['summary'] = str(payload.get('summary') or '结构化审计日志')
    parsed['extra'] = payload.get('extra') if isinstance(payload.get('extra'), dict) else {}
    changed_fields = payload.get('changed_fields') or []
    if isinstance(changed_fields, list):
        parsed['changed_fields'] = [str(i) for i in changed_fields]
    before = payload.get('before')
    after = payload.get('after')
    extra = payload.get('extra')
    if before not in (None, ''):
        parsed['before_pretty'] = json.dumps(before, ensure_ascii=False, indent=2)
    if after not in (None, ''):
        parsed['after_pretty'] = json.dumps(after, ensure_ascii=False, indent=2)
    if extra not in (None, '', {}, []):
        parsed['extra_pretty'] = json.dumps(extra, ensure_ascii=False, indent=2)
    return parsed


def _attach_transfer_allocations_display(orders):
    for order in orders:
        grouped = defaultdict(int)
        for alloc in order.transfer_allocations_target.all():
            if alloc.status not in ['locked', 'consumed']:
                continue
            if not alloc.source_order_id:
                continue
            grouped[alloc.source_order.order_no] += int(alloc.quantity or 0)
        order.transfer_allocations_display = [
            {'order_no': order_no, 'quantity': qty}
            for order_no, qty in grouped.items()
            if qty > 0
        ]


def _is_transfer_source_order_active(order):
    """
    判断订单是否仍在转寄链路中作为来源单被使用。
    命中时应在订单中心禁用“标记归还”，要求去转寄中心操作。
    """
    return TransferAllocation.objects.filter(
        source_order=order,
        status__in=['locked', 'consumed'],
        target_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
    ).exists()


def _consume_transfer_allocations_for_transfer(transfer, operator=None):
    """
    精确消耗转寄挂靠锁：
    - 仅消耗当前 transfer 的 source->target->sku 对应 locked 记录
    - 支持部分消耗（拆分数量，保留剩余 locked）
    Returns:
        (consumed_qty, shortfall_qty)
    """
    required_qty = max(int(transfer.quantity or 0), 0)
    if required_qty <= 0:
        return 0, 0

    consumed_qty = 0
    remaining = required_qty
    allocations = list(
        TransferAllocation.objects.select_for_update().filter(
            source_order_id=transfer.order_from_id,
            target_order_id=transfer.order_to_id,
            sku_id=transfer.sku_id,
            status='locked',
        ).order_by('created_at', 'id')
    )

    for alloc in allocations:
        if remaining <= 0:
            break
        alloc_qty = int(alloc.quantity or 0)
        if alloc_qty <= 0:
            continue

        if alloc_qty <= remaining:
            alloc.status = 'consumed'
            alloc.save(update_fields=['status', 'updated_at'])
            consumed_qty += alloc_qty
            remaining -= alloc_qty
            continue

        # 发生部分消耗时，拆分为 consumed + locked 两段，避免误消耗超额数量
        TransferAllocation.objects.create(
            source_order=alloc.source_order,
            target_order=alloc.target_order,
            sku=alloc.sku,
            quantity=remaining,
            target_event_date=alloc.target_event_date,
            window_start=alloc.window_start,
            window_end=alloc.window_end,
            distance_score=alloc.distance_score,
            status='consumed',
            created_by=operator or alloc.created_by,
        )
        alloc.quantity = alloc_qty - remaining
        alloc.save(update_fields=['quantity', 'updated_at'])
        consumed_qty += remaining
        remaining = 0

    return consumed_qty, remaining


def _log_transfer_action(user, action, target, details):
    AuditService.log_with_diff(
        user=user,
        action=action,
        module='转寄',
        target=target,
        summary=details,
        before={},
        after={},
    )


def _request_high_risk_approval(request, *, action_code, module, target_type, target_id, target_label, summary, payload):
    settings = get_system_settings()
    required_count = int(settings.get('approval_required_count_default', 1) or 1)
    if action_code == 'order.force_cancel':
        required_count = int(settings.get('approval_required_count_order_force_cancel', required_count) or required_count)
    elif action_code == 'transfer.cancel_task':
        required_count = int(settings.get('approval_required_count_transfer_cancel_task', required_count) or required_count)
    elif action_code == 'unit.dispose':
        required_count = int(settings.get('approval_required_count_unit_dispose', required_count) or required_count)
        action_type = (payload or {}).get('action_type')
        if action_type == 'disassemble':
            required_count = int(settings.get('approval_required_count_unit_disassemble', required_count) or required_count)
        elif action_type == 'scrap':
            required_count = int(settings.get('approval_required_count_unit_scrap', required_count) or required_count)
    raw_map = settings.get('approval_required_count_map', '{}')
    if isinstance(raw_map, str):
        try:
            rule_map = json.loads(raw_map) if raw_map.strip() else {}
        except Exception:
            rule_map = {}
    elif isinstance(raw_map, dict):
        rule_map = raw_map
    else:
        rule_map = {}
    if action_code == 'unit.dispose':
        action_type = (payload or {}).get('action_type')
        scoped_key = f'{action_code}.{action_type}' if action_type in ['disassemble', 'scrap'] else ''
        scoped_count = rule_map.get(scoped_key)
        try:
            if scoped_count is not None:
                required_count = int(scoped_count)
        except (TypeError, ValueError):
            pass
    mapped_count = rule_map.get(action_code)
    try:
        if mapped_count is not None:
            required_count = int(mapped_count)
    except (TypeError, ValueError):
        pass
    required_count = min(max(int(required_count or 1), 1), 5)
    task, created = ApprovalService.create_or_get_pending(
        action_code=action_code,
        module=module,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        summary=summary,
        payload=payload,
        requested_by=request.user,
        required_review_count=required_count,
    )
    AuditService.log_with_diff(
        user=request.user,
        action='create',
        module='审批',
        target=task.task_no,
        summary='提交审批申请',
        before={},
        after={
            'task_no': task.task_no,
            'action_code': action_code,
            'target_type': target_type,
            'target_id': target_id,
            'status': task.status,
        },
        extra={
            'created': created,
            'module': module,
            'target_label': target_label,
            'payload': payload,
        },
    )
    if created:
        messages.success(request, f'已提交审批申请：{task.task_no}')
    else:
        messages.warning(request, f'已存在待审批单：{task.task_no}')
    return task, created


def _execute_order_cancel(order, reason, operator):
    pre_status = order.status
    OrderService.cancel_order(order.id, reason, operator)
    if pre_status in ['delivered', 'in_use', 'returned']:
        RiskEventService.create_event(
            event_type='delivered_order_cancel',
            level='high',
            module='订单',
            title='已履约订单被取消',
            description=f'订单 {order.order_no} 在状态 {dict(Order.STATUS_CHOICES).get(pre_status, pre_status)} 下被取消',
            event_data={
                'order_id': order.id,
                'order_no': order.order_no,
                'pre_status': pre_status,
                'reason': reason,
            },
            order=order,
            detected_by=operator,
        )
    _maybe_create_frequent_cancel_risk(operator)
    return pre_status


def _execute_transfer_cancel(transfer, operator, reason='手动取消'):
    if transfer.status != 'pending':
        raise ValueError('仅待执行任务可取消')
    before_transfer = _snapshot_transfer_audit(transfer)
    transfer.status = 'cancelled'
    transfer.notes = (transfer.notes + '\n' if transfer.notes else '') + reason
    transfer.save(update_fields=['status', 'notes', 'updated_at'])
    AuditService.log_with_diff(
        user=operator,
        action='status_change',
        module='转寄',
        target=f'任务#{transfer.id}',
        summary='取消转寄任务',
        before=before_transfer,
        after=_snapshot_transfer_audit(transfer),
        extra={'reason': reason},
    )


def _save_transfer_recommendation_log(order, sku_id, before_source_ids, plan, operator, trigger_type='recommend'):
    sku = SKU.objects.filter(id=sku_id).first()
    if not sku:
        return
    settings = get_system_settings()
    weight_date = int(settings.get('transfer_score_weight_date', 100) or 100)
    weight_conf = int(settings.get('transfer_score_weight_confidence', 10) or 10)
    weight_distance = int(settings.get('transfer_score_weight_distance', 1) or 1)
    selected_alloc = (plan.get('allocations') or [])
    selected_source_id = None
    selected_source_no = ''
    if selected_alloc:
        selected_source_id = selected_alloc[0].get('source_order_id')
        selected_source_no = selected_alloc[0].get('source_order_no') or ''
    confidence_rank_map = {'high': 0, 'medium': 1, 'low': 2}
    candidates = []
    selected_rank = None
    selected_score_total = None
    for idx, c in enumerate((plan.get('candidates') or []), start=1):
        source = c.get('source_order')
        if not source:
            continue
        date_gap_score = int(c.get('date_gap_score') or 0)
        distance_raw = c.get('distance_score') or 0
        try:
            distance_value = float(distance_raw)
        except (TypeError, ValueError):
            distance_value = 0.0
        distance_mode = c.get('distance_mode') or ''
        distance_confidence = c.get('distance_confidence') or 'low'
        confidence_rank = confidence_rank_map.get(distance_confidence, 2)
        score_date = date_gap_score * weight_date
        score_confidence = confidence_rank * weight_conf
        score_distance = distance_value * weight_distance
        score_total = round(score_date + score_confidence + score_distance, 4)
        candidates.append({
            'source_order_id': source.id,
            'source_order_no': source.order_no,
            'source_event_date': source.event_date.strftime('%Y-%m-%d') if source.event_date else '',
            'available_qty': int(c.get('available_qty') or 0),
            'distance_score': str(distance_raw),
            'date_gap_score': date_gap_score,
            'distance_mode': distance_mode,
            'distance_confidence': distance_confidence,
            'score_rank': idx,
            'score_total': score_total,
            'score_components': {
                'date_gap_weighted': score_date,
                'confidence_weighted': score_confidence,
                'distance_weighted': round(score_distance, 4),
                'date_gap_score': date_gap_score,
                'distance_score': distance_value,
                'confidence_rank': confidence_rank,
            },
            'source_city': c.get('source_city') or '',
            'target_city': c.get('target_city') or '',
        })
        if selected_source_id and selected_source_id == source.id and selected_rank is None:
            selected_rank = idx
            selected_score_total = score_total
    decision_reason = '无可用候选，仓库补量'
    if selected_source_id:
        if selected_rank == 1:
            decision_reason = '命中最优候选（评分第1）'
        elif selected_rank:
            decision_reason = f'命中候选（评分第{selected_rank}）'
        else:
            decision_reason = '命中候选（评分快照缺失）'
    TransferRecommendationLog.objects.create(
        order=order,
        sku=sku,
        trigger_type=trigger_type,
        target_event_date=order.event_date,
        target_address=order.delivery_address or '',
        before_source_order_ids=[int(x) for x in (before_source_ids or []) if x],
        selected_source_order_id=selected_source_id,
        selected_source_order_no=selected_source_no,
        warehouse_needed=int(plan.get('warehouse_needed') or 0),
        candidates=candidates,
        score_summary={
            'candidate_count': len(candidates),
            'buffer_days': int(plan.get('buffer_days') or 0),
            'selected_rank': selected_rank,
            'selected_score_total': selected_score_total,
            'weights': {
                'date_gap': weight_date,
                'confidence': weight_conf,
                'distance': weight_distance,
            },
            'decision_reason': decision_reason,
        },
        operator=operator,
    )


def _maybe_create_frequent_cancel_risk(user):
    now = timezone.now()
    window_start = now - timedelta(hours=24)
    cancel_count = AuditLog.objects.filter(
        user=user,
        module='订单',
        action='status_change',
        created_at__gte=window_start,
        details__icontains='取消订单',
    ).count()
    threshold = 3
    if cancel_count < threshold:
        return
    RiskEventService.create_event(
        event_type='frequent_cancel',
        level='medium',
        module='订单',
        title='高频取消预警',
        description=f'用户 {user.username} 在24小时内已执行 {cancel_count} 次取消操作',
        event_data={
            'username': user.username,
            'window_hours': 24,
            'cancel_count': cancel_count,
            'threshold': threshold,
        },
        detected_by=user,
    )


def _snapshot_order_audit(order):
    return {
        'id': order.id,
        'order_no': order.order_no,
        'status': order.status,
        'customer_name': order.customer_name,
        'customer_phone': order.customer_phone,
        'event_date': order.event_date,
        'ship_tracking': order.ship_tracking,
        'return_tracking': order.return_tracking,
        'total_amount': order.total_amount,
        'deposit_paid': order.deposit_paid,
        'balance': order.balance,
    }


def _snapshot_transfer_audit(transfer):
    return {
        'id': transfer.id,
        'status': transfer.status,
        'order_from_id': transfer.order_from_id,
        'order_to_id': transfer.order_to_id,
        'sku_id': transfer.sku_id,
        'quantity': transfer.quantity,
        'gap_days': transfer.gap_days,
        'notes': transfer.notes,
    }


def _get_unit_status_display(status):
    return dict(InventoryUnit.STATUS_CHOICES).get(status, status)


def _compute_unit_health(unit_status, hop_count, outbound_days, warn_reason='', latest_event_type=''):
    """
    单套健康分（0~100）：
    - 基础分 100，按风险扣分。
    - 仅用于看板可视化，不影响业务主流程判定。
    """
    score = 100

    # 链路复杂度：转寄节点越多，风险越高
    score -= min(int(hop_count or 0) * 4, 32)

    # 在外时长：长期在外存在损耗/丢件风险
    score -= min(int(outbound_days or 0) * 2, 30)

    # 状态惩罚
    if unit_status == 'in_transit':
        score -= 5
    elif unit_status == 'maintenance':
        score -= 20
    elif unit_status == 'scrapped':
        score = 0

    # 预警惩罚
    reason = warn_reason or ''
    if '转寄节点>' in reason:
        score -= 10
    if '在途>' in reason:
        score -= 10
    if '待执行>' in reason:
        score -= 8
    if '转寄在途>' in reason:
        score -= 8
    if '异常节点' in reason:
        score -= 12
    if '部件异常' in reason:
        score -= 12

    if latest_event_type == 'EXCEPTION':
        score -= 10

    score = max(min(int(score), 100), 0)
    if score >= 80:
        level = '健康'
    elif score >= 60:
        level = '关注'
    else:
        level = '风险'
    return score, level


def _build_unit_chain_text(unit):
    moves = UnitMovement.objects.filter(unit=unit).select_related('from_order', 'to_order').order_by('event_time')
    chain = []
    for mv in moves:
        label = dict(UnitMovement.EVENT_CHOICES).get(mv.event_type, mv.event_type)
        if mv.from_order and mv.to_order:
            chain.append(f"{label}({mv.from_order.order_no}->{mv.to_order.order_no})")
        elif mv.to_order:
            chain.append(f"{label}(->{mv.to_order.order_no})")
        elif mv.from_order:
            chain.append(f"{label}({mv.from_order.order_no}->)")
        else:
            chain.append(label)
    return " -> ".join(chain) if chain else "-"


def _parse_sku_components_post(request):
    """解析SKU BOM表单数据"""
    part_ids = request.POST.getlist('component_part_id[]')
    qty_list = request.POST.getlist('component_qty[]')
    notes_list = request.POST.getlist('component_notes[]')
    max_len = max(len(part_ids), len(qty_list), len(notes_list), 0)
    parsed = []
    for idx in range(max_len):
        part_id_raw = (part_ids[idx] if idx < len(part_ids) else '').strip()
        qty_raw = (qty_list[idx] if idx < len(qty_list) else '').strip()
        notes = (notes_list[idx] if idx < len(notes_list) else '').strip()
        if not part_id_raw:
            continue
        if not part_id_raw.isdigit():
            raise ValueError('部件组成存在非法部件ID')
        qty = int(qty_raw or '1')
        if qty <= 0:
            raise ValueError('部件组成单套用量必须大于0')
        parsed.append({
            'part_id': int(part_id_raw),
            'quantity_per_set': qty,
            'notes': notes[:200],
        })

    if not parsed:
        return []

    part_ids = [item['part_id'] for item in parsed]
    valid_ids = set(
        Part.objects.filter(id__in=part_ids, is_active=True).values_list('id', flat=True)
    )
    invalid_ids = [str(pid) for pid in part_ids if pid not in valid_ids]
    if invalid_ids:
        raise ValueError(f"部件不存在或已停用：{', '.join(invalid_ids)}")

    # 同一个部件去重聚合（用量累加）
    merged = {}
    for row in parsed:
        pid = row['part_id']
        if pid not in merged:
            merged[pid] = row
            continue
        merged[pid]['quantity_per_set'] += row['quantity_per_set']
        if row['notes'] and not merged[pid]['notes']:
            merged[pid]['notes'] = row['notes']
    return list(merged.values())


def _build_sku_gallery_snapshot(sku):
    gallery = []
    for image in sku.images.all().order_by('sort_order', 'id'):
        gallery.append({
            'id': image.id,
            'key': image.image_key or '',
            'url': image.image_url,
            'sort_order': int(image.sort_order or 0),
            'is_cover': bool(image.is_cover),
        })
    return gallery


def _parse_sku_gallery_post(request):
    raw = (request.POST.get('gallery_payload') or '').strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f'图片画廊数据格式错误：{exc}') from exc
    if not isinstance(payload, list):
        raise ValueError('图片画廊数据格式错误')

    normalized = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        key = (item.get('key') or '').strip()
        if not key:
            continue
        if not StorageService.is_valid_sku_key(key):
            raise ValueError('图片画廊包含非法图片 Key')
        normalized.append({
            'key': key,
            'sort_order': int(item.get('sort_order') or index),
            'is_cover': bool(item.get('is_cover')),
        })

    if normalized and not any(item['is_cover'] for item in normalized):
        normalized[0]['is_cover'] = True
    return normalized


def _save_sku_gallery(sku, gallery_payload):
    if gallery_payload is None:
        return
    sku.images.all().delete()
    if not gallery_payload:
        return
    SKUImage.objects.bulk_create([
        SKUImage(
            sku=sku,
            image_key=item['key'],
            sort_order=item['sort_order'],
            is_cover=item['is_cover'],
        )
        for item in gallery_payload
    ])


def _parse_maintenance_items_post(request):
    """解析维修换件工单明细"""
    old_part_ids = request.POST.getlist('old_part_id[]')
    new_part_ids = request.POST.getlist('new_part_id[]')
    qty_list = request.POST.getlist('replace_quantity[]')
    notes_list = request.POST.getlist('item_notes[]')
    max_len = max(len(old_part_ids), len(new_part_ids), len(qty_list), len(notes_list), 0)
    parsed = []
    for idx in range(max_len):
        old_raw = (old_part_ids[idx] if idx < len(old_part_ids) else '').strip()
        new_raw = (new_part_ids[idx] if idx < len(new_part_ids) else '').strip()
        qty_raw = (qty_list[idx] if idx < len(qty_list) else '').strip()
        notes = (notes_list[idx] if idx < len(notes_list) else '').strip()
        if not old_raw and not new_raw:
            continue
        if not old_raw.isdigit() or not new_raw.isdigit():
            raise ValueError('维修明细存在非法部件ID')
        qty = int(qty_raw or '0')
        if qty <= 0:
            raise ValueError('维修明细更换数量必须大于 0')
        parsed.append({
            'old_part_id': int(old_raw),
            'new_part_id': int(new_raw),
            'replace_quantity': qty,
            'notes': notes[:200],
        })
    return parsed


def login_view(request):
    """登录页面"""
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, '用户名或密码错误')

    return render(request, 'login.html')


def logout_view(request):
    """登出"""
    logout(request)
    return redirect('login')


@login_required
@require_permission('dashboard', 'view')
def dashboard(request):
    """工作台首页"""
    stats = get_dashboard_stats_payload(include_transfer_available=True)
    view_role = ''
    if request.user.role in ['admin', 'manager']:
        view_role = (request.GET.get('view_role') or '').strip()
    role_dashboard = get_role_dashboard_payload(
        request.user,
        view_role=view_role or None,
        base_stats=stats,
    )
    dashboard_followup_reminder = None
    if role_dashboard.get('reservation_followup'):
        today_followup = int(role_dashboard['reservation_followup'].get('today_count') or 0)
        overdue_followup = int(role_dashboard['reservation_followup'].get('overdue_count') or 0)
        if overdue_followup or today_followup:
            dashboard_followup_reminder = {
                'today_count': today_followup,
                'overdue_count': overdue_followup,
                'severity': 'danger' if overdue_followup else 'warning',
                'title': '预定跟进提醒',
                'message': (
                    f'有 {overdue_followup} 张预定单已逾期未联系，请尽快跟进'
                    if overdue_followup
                    else f'今天有 {today_followup} 张预定单需要联系客户确认细节'
                ),
                'detail_query': 'contact=overdue' if overdue_followup else 'contact=today',
                'dismiss_key': f'dashboard-followup-reminder:{request.user.id}:{timezone.localdate().isoformat()}',
            }

    # 最近订单
    recent_orders = Order.objects.select_related('created_by').prefetch_related(
        'transfer_allocations_target__source_order',
    ).order_by('-created_at')[:5]
    _attach_transfer_allocations_display(recent_orders)

    # 库存不足的部件
    low_stock_parts = Part.objects.filter(
        is_active=True,
        current_stock__lt=F('safety_stock')
    ).order_by('current_stock')[:5]

    context = {
        'stats': stats,
        'role_dashboard': role_dashboard,
        'recent_orders': recent_orders,
        'low_stock_parts': low_stock_parts,
        'view_role': view_role,
        'dashboard_followup_reminder': dashboard_followup_reminder,
        'role_view_options': [
            {'value': 'admin', 'label': '超级管理员'},
            {'value': 'manager', 'label': '业务经理'},
            {'value': 'warehouse_manager', 'label': '仓库主管'},
            {'value': 'warehouse_staff', 'label': '仓库操作员'},
            {'value': 'customer_service', 'label': '客服'},
        ] if request.user.role in ['admin', 'manager'] else [],
    }
    return render(request, 'dashboard.html', context)


@login_required
@require_permission('workbench', 'view')
def workbench(request):
    """订单处理入口已合并到订单中心，保留旧路由并跳转。"""
    return redirect('orders_list')


@login_required
@require_permission('reservations', 'view')
def reservations_list(request):
    """预定单列表"""
    reservations = Reservation.objects.select_related('sku', 'created_by', 'owner', 'converted_order').all()
    if not request.user.is_superuser and request.user.role == 'customer_service':
        reservations = reservations.filter(owner=request.user)
    status_summary = {
        'pending_info': reservations.filter(status='pending_info').count(),
        'ready_to_convert': reservations.filter(status='ready_to_convert').count(),
        'converted': reservations.filter(status='converted').count(),
        'cancelled': reservations.filter(status='cancelled').count(),
    }

    status_filter = (request.GET.get('status') or '').strip()
    contact_filter = (request.GET.get('contact') or '').strip()
    journey_filter = (request.GET.get('journey') or '').strip()
    keyword = (request.GET.get('keyword') or '').strip()
    owner_filter = (request.GET.get('owner') or '').strip()
    source_filter = (request.GET.get('source') or '').strip()
    status_summary['awaiting_shipment'] = reservations.filter(
        status='converted',
        converted_order__status__in=['pending', 'confirmed'],
    ).count()
    status_summary['awaiting_shipment_overdue'] = reservations.filter(
        status='converted',
        converted_order__status__in=['pending', 'confirmed'],
        converted_order__ship_date__isnull=False,
        converted_order__ship_date__lte=timezone.localdate(),
        converted_order__ship_tracking='',
    ).count()
    status_summary['balance_due'] = reservations.filter(
        status='converted',
        converted_order__isnull=False,
        converted_order__balance__gt=Decimal('0.00'),
    ).exclude(
        converted_order__status='cancelled',
    ).count()
    if status_filter:
        reservations = reservations.filter(status=status_filter)
    followup_lead_days = int(get_system_settings().get('reservation_followup_lead_days', 7) or 7)
    target_event_date = timezone.localdate() + timedelta(days=followup_lead_days)
    if contact_filter == 'today':
        reservations = reservations.filter(status__in=['pending_info', 'ready_to_convert'], event_date=target_event_date)
    elif contact_filter == 'overdue':
        reservations = reservations.filter(status__in=['pending_info', 'ready_to_convert'], event_date__lt=target_event_date)
    elif contact_filter == 'pending':
        reservations = reservations.filter(status__in=['pending_info', 'ready_to_convert'], event_date__gt=target_event_date)
    if journey_filter == 'awaiting_shipment':
        reservations = reservations.filter(status='converted', converted_order__status__in=['pending', 'confirmed'])
    elif journey_filter == 'awaiting_shipment_overdue':
        reservations = reservations.filter(
            status='converted',
            converted_order__status__in=['pending', 'confirmed'],
            converted_order__ship_date__isnull=False,
            converted_order__ship_date__lte=timezone.localdate(),
            converted_order__ship_tracking='',
        )
    elif journey_filter == 'balance_due':
        reservations = reservations.filter(
            status='converted',
            converted_order__isnull=False,
            converted_order__balance__gt=Decimal('0.00'),
        ).exclude(
            converted_order__status='cancelled',
        )
    elif journey_filter == 'in_fulfillment':
        reservations = reservations.filter(status='converted', converted_order__status__in=['delivered', 'in_use', 'returned'])
    elif journey_filter == 'completed':
        reservations = reservations.filter(status='converted', converted_order__status='completed')
    if owner_filter and request.user.role in ['admin', 'manager']:
        reservations = reservations.filter(owner_id=owner_filter)
    if keyword:
        reservations = reservations.filter(
            Q(reservation_no__icontains=keyword) |
            Q(customer_wechat__icontains=keyword) |
            Q(customer_name__icontains=keyword) |
            Q(customer_phone__icontains=keyword) |
            Q(city__icontains=keyword) |
            Q(sku__name__icontains=keyword) |
            Q(sku__code__icontains=keyword)
        )
    if source_filter:
        reservations = reservations.filter(source=source_filter)

    reservations = reservations.order_by('-created_at')
    reservations_page = Paginator(reservations, _get_page_size('page_size_reservations')).get_page(request.GET.get('page'))
    context = {
        'reservations': reservations_page,
        'reservations_page': reservations_page,
        'status_filter': status_filter,
        'contact_filter': contact_filter,
        'journey_filter': journey_filter,
        'keyword': keyword,
        'owner_filter': owner_filter,
        'source_filter': source_filter,
        'pagination_query': _build_querystring(request, ['page']),
        'status_choices': Reservation.STATUS_CHOICES,
        'status_summary': status_summary,
        'owner_choices': _get_reservation_owner_candidates() if request.user.role in ['admin', 'manager'] else [],
        'followup_lead_days': followup_lead_days,
    }
    return render(request, 'reservations/list.html', context)


@login_required
@require_permission('reservations', 'create')
def reservation_create(request):
    """创建预定单"""
    conflict_summary = None
    if request.method == 'POST':
        try:
            payload = _parse_reservation_form_payload(request)
            conflict_summary = _build_reservation_conflict_summary(payload=payload)
            reservation = Reservation.objects.create(
                customer_wechat=payload['customer_wechat'],
                customer_name=payload['customer_name'],
                customer_phone=payload['customer_phone'],
                city=payload['city'],
                sku_id=payload['sku_id'],
                quantity=payload['quantity'],
                event_date=payload['event_date'],
                deposit_amount=payload['deposit_amount'],
                status=payload['status'],
                notes=payload['notes'],
                created_by=request.user,
                owner_id=(payload['owner_id'] if request.user.role in ['admin', 'manager'] else None) or request.user.id,
            )
            _create_reservation_finance_transaction(
                reservation=reservation,
                transaction_type='reservation_deposit_received',
                amount=reservation.deposit_amount,
                user=request.user,
                notes='创建预定单收取订金',
            )
            AuditService.log_with_diff(
                user=request.user,
                action='create',
                module='预定单',
                target=reservation.reservation_no,
                summary='创建预定单',
                before={},
                after=_snapshot_reservation_audit(reservation),
            )
            messages.success(request, f'预定单创建成功：{reservation.reservation_no}')
            return redirect('reservations_list')
        except ValueError as exc:
            messages.error(request, str(exc))
            if 'payload' in locals():
                conflict_summary = _build_reservation_conflict_summary(payload=payload)
        except Exception as exc:
            messages.error(request, f'预定单创建失败：{str(exc)}')
            if 'payload' in locals():
                conflict_summary = _build_reservation_conflict_summary(payload=payload)

    context = {
        'mode': 'create',
        'skus': SKU.objects.filter(is_active=True).order_by('code'),
        'status_choices': Reservation.STATUS_CHOICES,
        'conflict_summary': conflict_summary,
        'owner_choices': _get_reservation_owner_candidates() if request.user.role in ['admin', 'manager'] else [],
    }
    return render(request, 'reservations/form.html', context)


@login_required
@require_permission('reservations', 'update')
def reservation_edit(request, reservation_id):
    """编辑预定单"""
    reservation = get_object_or_404(Reservation.objects.select_related('sku', 'converted_order', 'owner'), id=reservation_id)
    conflict_summary = _build_reservation_conflict_summary(reservation=reservation)
    if not request.user.is_superuser and request.user.role == 'customer_service' and reservation.owner_id != request.user.id:
        messages.error(request, '您没有权限编辑该预定单')
        return redirect('reservations_list')

    if request.method == 'POST':
        before_snapshot = _snapshot_reservation_audit(reservation)
        previous_deposit = reservation.deposit_amount or Decimal('0.00')
        try:
            payload = _parse_reservation_form_payload(request)
            conflict_summary = get_reservation_conflict_summary(
                payload['sku_id'],
                payload['event_date'],
                quantity=payload['quantity'],
                exclude_reservation_id=reservation.id,
            )
            owner_id = payload.pop('owner_id', None)
            for field, value in payload.items():
                setattr(reservation, field, value)
            if request.user.role in ['admin', 'manager']:
                reservation.owner_id = owner_id or reservation.owner_id or request.user.id
            else:
                reservation.owner_id = reservation.owner_id or request.user.id
            reservation.save()
            delta = (reservation.deposit_amount or Decimal('0.00')) - previous_deposit
            if delta > 0:
                _create_reservation_finance_transaction(
                    reservation=reservation,
                    transaction_type='reservation_deposit_received',
                    amount=delta,
                    user=request.user,
                    notes='编辑预定单补收订金',
                )
            elif delta < 0:
                _create_reservation_finance_transaction(
                    reservation=reservation,
                    transaction_type='reservation_deposit_refund',
                    amount=abs(delta),
                    user=request.user,
                    notes='编辑预定单退还订金差额',
                )
            AuditService.log_with_diff(
                user=request.user,
                action='update',
                module='预定单',
                target=reservation.reservation_no,
                summary='编辑预定单',
                before=before_snapshot,
                after=_snapshot_reservation_audit(reservation),
            )
            messages.success(request, f'预定单已更新：{reservation.reservation_no}')
            return redirect('reservations_list')
        except ValueError as exc:
            messages.error(request, str(exc))
            if 'payload' in locals():
                conflict_summary = get_reservation_conflict_summary(
                    payload.get('sku_id'),
                    payload.get('event_date'),
                    quantity=payload.get('quantity') or 1,
                    exclude_reservation_id=reservation.id,
                )
        except Exception as exc:
            messages.error(request, f'预定单更新失败：{str(exc)}')
            if 'payload' in locals():
                conflict_summary = get_reservation_conflict_summary(
                    payload.get('sku_id'),
                    payload.get('event_date'),
                    quantity=payload.get('quantity') or 1,
                    exclude_reservation_id=reservation.id,
                )

    context = {
        'mode': 'edit',
        'reservation': reservation,
        'skus': SKU.objects.filter(is_active=True).order_by('code'),
        'status_choices': Reservation.STATUS_CHOICES,
        'conflict_summary': conflict_summary,
        'owner_choices': _get_reservation_owner_candidates() if request.user.role in ['admin', 'manager'] else [],
    }
    return render(request, 'reservations/form.html', context)


@login_required
@require_permission('reservations', 'update')
def reservation_cancel(request, reservation_id):
    """取消预定单"""
    reservation = get_object_or_404(Reservation, id=reservation_id)
    if request.method != 'POST':
        return redirect('reservations_list')
    if not request.user.is_superuser and request.user.role == 'customer_service' and reservation.owner_id != request.user.id:
        messages.error(request, '您没有权限操作该预定单')
        return redirect('reservations_list')
    if reservation.status in ['converted', 'refunded']:
        messages.error(request, '当前状态不可取消')
        return redirect('reservations_list')
    before_snapshot = _snapshot_reservation_audit(reservation)
    reservation.status = 'cancelled'
    reservation.notes = (reservation.notes + '\n' if reservation.notes else '') + (request.POST.get('reason') or '手动取消')
    reservation.save(update_fields=['status', 'notes', 'updated_at'])
    AuditService.log_with_diff(
        user=request.user,
        action='status_change',
        module='预定单',
        target=reservation.reservation_no,
        summary='取消预定单',
        before=before_snapshot,
        after=_snapshot_reservation_audit(reservation),
    )
    messages.success(request, f'预定单已取消：{reservation.reservation_no}')
    return redirect('reservations_list')


@login_required
@require_permission('reservations', 'update')
def reservations_bulk_update_status(request):
    """批量更新预定单跟进状态，仅限安全状态流转。"""
    if request.method != 'POST':
        return redirect('reservations_list')

    target_status = (request.POST.get('status') or '').strip()
    if target_status not in ['pending_info', 'ready_to_convert']:
        messages.error(request, '批量状态无效')
        return redirect('reservations_list')

    reservation_ids = [str(i).strip() for i in request.POST.getlist('ids[]') if str(i).strip()]
    if not reservation_ids:
        messages.error(request, '请先勾选预定单')
        return redirect('reservations_list')

    reservations = Reservation.objects.filter(id__in=reservation_ids)
    if not request.user.is_superuser and request.user.role == 'customer_service':
        reservations = reservations.filter(owner=request.user)

    updated = 0
    skipped = 0
    for reservation in reservations:
        if reservation.status in ['converted', 'refunded']:
            skipped += 1
            continue
        if reservation.status == target_status:
            continue
        before_snapshot = _snapshot_reservation_audit(reservation)
        reservation.status = target_status
        reservation.save(update_fields=['status', 'updated_at'])
        AuditService.log_with_diff(
            user=request.user,
            action='status_change',
            module='预定单',
            target=reservation.reservation_no,
            summary=f'批量标记为{reservation.get_status_display()}',
            before=before_snapshot,
            after=_snapshot_reservation_audit(reservation),
            extra={'source': 'bulk_followup'},
        )
        updated += 1

    if updated:
        messages.success(request, f'批量跟进完成：已更新 {updated} 条')
    elif skipped:
        messages.warning(request, '所选预定单均不可批量修改')
    else:
        messages.info(request, '所选预定单无需变更')
    return redirect('reservations_list')


@login_required
@require_permission('reservations', 'update')
def reservations_bulk_transfer_owner(request):
    """批量转交预定单负责人，仅管理角色可用。"""
    if request.method != 'POST':
        return redirect('reservations_list')
    if request.user.role not in ['admin', 'manager']:
        messages.error(request, '您没有权限转交预定单负责人')
        return redirect('reservations_list')

    owner_id = (request.POST.get('owner_id') or '').strip()
    if not owner_id.isdigit():
        messages.error(request, '请选择新的负责人')
        return redirect('reservations_list')
    new_owner = get_object_or_404(User, id=int(owner_id), is_active=True)

    reservation_ids = [str(i).strip() for i in request.POST.getlist('ids[]') if str(i).strip()]
    if not reservation_ids:
        messages.error(request, '请先勾选要转交的预定单')
        return redirect('reservations_list')

    transfer_reason = (request.POST.get('transfer_reason') or '').strip()
    reservations = Reservation.objects.filter(id__in=reservation_ids)
    updated = 0
    for reservation in reservations:
        if reservation.status in ['converted', 'cancelled', 'refunded']:
            continue
        if reservation.owner_id == new_owner.id:
            continue
        before_snapshot = _snapshot_reservation_audit(reservation)
        reservation.owner = new_owner
        reservation.save(update_fields=['owner', 'updated_at'])
        AuditService.log_with_diff(
            user=request.user,
            action='update',
            module='预定单',
            target=reservation.reservation_no,
            summary='批量转交负责人',
            before=before_snapshot,
            after=_snapshot_reservation_audit(reservation),
            extra={'reason': transfer_reason, 'source': 'bulk_transfer_owner'},
        )
        updated += 1

    if updated:
        messages.success(request, f'批量转交完成：已转交 {updated} 条')
    else:
        messages.info(request, '所选预定单无需转交')
    return redirect('reservations_list')


@login_required
@require_permission('reservations', 'update')
def reservation_refund(request, reservation_id):
    """退款并关闭预定单"""
    reservation = get_object_or_404(Reservation, id=reservation_id)
    if request.method != 'POST':
        return redirect('reservations_list')
    if not request.user.is_superuser and request.user.role == 'customer_service' and reservation.owner_id != request.user.id:
        messages.error(request, '您没有权限操作该预定单')
        return redirect('reservations_list')
    if reservation.status == 'converted':
        messages.error(request, '已转正式订单的预定单不能直接退款')
        return redirect('reservations_list')
    before_snapshot = _snapshot_reservation_audit(reservation)
    refund_amount = reservation.deposit_amount or Decimal('0.00')
    _create_reservation_finance_transaction(
        reservation=reservation,
        transaction_type='reservation_deposit_refund',
        amount=refund_amount,
        user=request.user,
        notes=(request.POST.get('reason') or '预定单退款'),
    )
    reservation.deposit_amount = Decimal('0.00')
    reservation.status = 'refunded'
    reservation.notes = (reservation.notes + '\n' if reservation.notes else '') + (request.POST.get('reason') or '预定单退款')
    reservation.save(update_fields=['deposit_amount', 'status', 'notes', 'updated_at'])
    AuditService.log_with_diff(
        user=request.user,
        action='status_change',
        module='预定单',
        target=reservation.reservation_no,
        summary='预定单退款',
        before=before_snapshot,
        after=_snapshot_reservation_audit(reservation),
        extra={'refund_amount': str(refund_amount)},
    )
    messages.success(request, f'预定单已退款：{reservation.reservation_no}')
    return redirect('reservations_list')


@login_required
@require_permission('reservations', 'view')
def reservation_detail(request, reservation_id):
    """预定单详情"""
    reservation = get_object_or_404(
        Reservation.objects.select_related('sku', 'created_by', 'owner', 'converted_order'),
        id=reservation_id,
    )
    if not request.user.is_superuser and request.user.role == 'customer_service' and reservation.owner_id != request.user.id:
        messages.error(request, '您没有权限查看该预定单')
        return redirect('reservations_list')

    finance_transactions = FinanceTransaction.objects.filter(reservation=reservation).select_related('created_by').order_by('-created_at')
    audit_logs = (
        AuditLog.objects.filter(module='预定单', target=reservation.reservation_no)
        .select_related('user')
        .order_by('-created_at')
    )
    for log in audit_logs:
        log.details_parsed = _parse_audit_details(log)

    context = {
        'reservation': reservation,
        'finance_transactions': finance_transactions,
        'audit_logs': audit_logs,
        'conflict_summary': _build_reservation_conflict_summary(reservation=reservation),
    }
    return render(request, 'reservations/detail.html', context)


@login_required
@require_permission('orders', 'view')
def orders_list(request):
    """订单列表"""
    orders, filters = _build_orders_base_queryset(request)
    orders_page = Paginator(orders, _get_page_size('page_size_orders')).get_page(request.GET.get('page'))
    _attach_orders_list_meta(orders_page.object_list, filters['today'])

    context = {
        'orders': orders_page,
        'orders_page': orders_page,
        'status_filter': filters['status_filter'],
        'keyword': filters['keyword'],
        'sla_filter': filters['sla_filter'],
        'source_filter': filters['source_filter'],
        'return_service_filter': filters['return_service_filter'],
        'return_payment_filter': filters['return_payment_filter'],
        'pickup_filter': filters['pickup_filter'],
        'pagination_query': _build_querystring(request, ['page']),
        'order_source_choices': Order.ORDER_SOURCE_CHOICES,
        'return_service_type_choices': Order.RETURN_SERVICE_TYPE_CHOICES,
        'return_service_payment_status_choices': Order.RETURN_SERVICE_PAYMENT_STATUS_CHOICES,
        'return_pickup_status_choices': Order.RETURN_PICKUP_STATUS_CHOICES,
    }
    return render(request, 'orders/list.html', context)


@login_required
@require_permission('orders', 'view')
def orders_export(request):
    orders, filters = _build_orders_base_queryset(request)
    order_list = list(orders)
    _attach_orders_list_meta(order_list, filters['today'])

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = 'attachment; filename="orders_export.csv"'
    writer = csv.writer(response)
    writer.writerow([
        '订单号', '客户姓名', '手机号', '微信号', '收货地址', 'SKU', '数量', '来源平台', '平台单号',
        '预定日期', '发货日期', '订单状态', '发货单号', '租金合计', '已收押金', '待收尾款', '备注'
    ])
    for order in order_list:
        items_summary = '；'.join(
            f'{item.sku.name} x{item.quantity}' for item in order.items.all()
        )
        writer.writerow([
            order.order_no,
            order.customer_name,
            order.customer_phone,
            order.customer_wechat,
            order.delivery_address,
            items_summary,
            sum(item.quantity for item in order.items.all()),
            dict(Order.ORDER_SOURCE_CHOICES).get(order.order_source, order.order_source),
            order.source_order_no,
            order.event_date,
            order.ship_date or '',
            order.get_status_display(),
            order.ship_tracking,
            order.total_amount,
            order.deposit_paid,
            order.balance,
            order.notes,
        ])
    return response


@login_required
@require_permission('orders', 'view')
def orders_import_template(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = 'attachment; filename="orders_import_template.csv"'
    writer = csv.writer(response)
    writer.writerow(OrderImportService.TEMPLATE_HEADERS)
    writer.writerow([
        '示例客户', '13800000000', '广东省深圳市南山区示例路 1 号', '中国风',
        '微信', '168', '微信', '200', '微信', '2026-03-20', '2026-03-15',
        '0', '已发货', 'SF123456789', '柳奕霏', '2026-03-15', '示例备注'
    ])
    return response


@login_required
@require_permission('orders', 'create')
def orders_import(request):
    skus = SKU.objects.filter(is_active=True).order_by('category', 'name')
    if request.method == 'POST':
        uploaded_file = request.FILES.get('import_file')
        default_sku_id = (request.POST.get('default_sku_id') or '').strip()
        action = (request.POST.get('import_action') or 'preview').strip()
        if not uploaded_file:
            messages.error(request, '请选择要导入的 CSV 或 XLSX 文件')
            return render(request, 'orders/import.html', {'skus': skus, 'default_sku_id': default_sku_id})
        try:
            if action == 'import':
                summary = OrderImportService.import_file(uploaded_file, request.user, default_sku_id=default_sku_id or None)
                if summary['created_count']:
                    messages.success(request, f"订单导入完成：成功导入 {summary['created_count']} 条")
                if summary['error_count']:
                    messages.warning(request, f"有 {summary['error_count']} 条导入失败，请检查页面错误明细")
                return render(request, 'orders/import.html', {
                    'skus': skus,
                    'default_sku_id': default_sku_id,
                    'import_summary': summary,
                })

            preview = OrderImportService.preview_file(uploaded_file, default_sku_id=default_sku_id or None)
            if preview['valid_count']:
                messages.success(request, f"预检查完成：可导入 {preview['valid_count']} 条")
            if preview['error_count']:
                messages.warning(request, f"预检查发现 {preview['error_count']} 条问题，请先处理")
            return render(request, 'orders/import.html', {
                'skus': skus,
                'default_sku_id': default_sku_id,
                'preview_summary': preview,
            })
        except Exception as e:
            messages.error(request, f'订单导入失败：{e}')
    return render(request, 'orders/import.html', {'skus': skus})


@login_required
@require_permission('orders', 'create')
def order_create(request):
    """创建订单"""
    source_reservation = None
    if request.method == 'POST':
        try:
            reservation_id_raw = (request.POST.get('reservation_id') or '').strip()
            if reservation_id_raw:
                source_reservation = get_object_or_404(Reservation.objects.select_related('sku'), id=reservation_id_raw)
                if not source_reservation.can_convert:
                    raise ValueError('该预定单当前状态不可转正式订单')
            # 获取订单明细
            sku_ids = request.POST.getlist('sku_id[]')
            quantities = request.POST.getlist('quantity[]')
            transfer_source_order_ids = request.POST.getlist('transfer_source_order_id[]')

            # 验证至少有一个明细
            if not sku_ids or not sku_ids[0]:
                messages.error(request, '请至少添加一个订单明细')
                skus = SKU.objects.filter(is_active=True)
                context = {
                    'skus': skus,
                    'mode': 'create',
                    'form_values': _extract_order_form_values(request),
                    'source_reservation': source_reservation,
                    **_build_order_form_meta(),
                }
                return render(request, 'orders/form.html', context)

            # 构建订单明细列表
            items = []
            for idx, (sku_id, quantity) in enumerate(zip(sku_ids, quantities)):
                if sku_id:  # 跳过空的明细行
                    source_order_id = ''
                    if idx < len(transfer_source_order_ids):
                        source_order_id = (transfer_source_order_ids[idx] or '').strip()
                    force_warehouse = source_order_id == '__warehouse__'
                    items.append({
                        'sku_id': int(sku_id),
                        'quantity': int(quantity) if quantity else 1,
                        'transfer_source_order_id': int(source_order_id) if (source_order_id and source_order_id.isdigit()) else None,
                        'force_warehouse': force_warehouse,
                    })

            # 构建订单数据
            data = {
                'customer_name': request.POST.get('customer_name'),
                'customer_phone': request.POST.get('customer_phone'),
                'customer_wechat': request.POST.get('customer_wechat', ''),
                'xianyu_order_no': request.POST.get('xianyu_order_no', ''),
                'order_source': request.POST.get('order_source', 'wechat'),
                'source_order_no': request.POST.get('source_order_no', ''),
                'customer_email': request.POST.get('customer_email', ''),
                'delivery_address': request.POST.get('delivery_address'),
                'return_address': request.POST.get('return_address', ''),
                'event_date': datetime.strptime(request.POST.get('event_date'), '%Y-%m-%d').date(),
                'rental_days': int(request.POST.get('rental_days', 1)),
                'return_service_type': request.POST.get('return_service_type', 'none'),
                'return_service_fee': request.POST.get('return_service_fee', '0'),
                'return_service_payment_status': request.POST.get('return_service_payment_status', 'unpaid'),
                'return_service_payment_channel': request.POST.get('return_service_payment_channel', ''),
                'return_service_payment_reference': request.POST.get('return_service_payment_reference', ''),
                'return_pickup_status': request.POST.get('return_pickup_status', 'not_required'),
                'notes': request.POST.get('notes', ''),
                'items': items
            }

            # 创建订单
            order = OrderService.create_order(data, request.user)
            if source_reservation:
                deposit_amount = source_reservation.deposit_amount or Decimal('0.00')
                if deposit_amount > 0:
                    order.deposit_paid = deposit_amount
                    order.save(update_fields=['deposit_paid', 'updated_at'])
                    FinanceTransaction.objects.create(
                        order=order,
                        transaction_type='reservation_deposit_applied',
                        amount=deposit_amount,
                        reference_no=source_reservation.reservation_no,
                        notes='由预定单订金自动结转为订单押金，不产生新增收款',
                        created_by=request.user,
                    )
                before_reservation = _snapshot_reservation_audit(source_reservation)
                source_reservation.status = 'converted'
                source_reservation.converted_order = order
                source_reservation.save(update_fields=['status', 'converted_order', 'updated_at'])
                AuditService.log_with_diff(
                    user=request.user,
                    action='status_change',
                    module='预定单',
                    target=source_reservation.reservation_no,
                    summary='预定单转正式订单',
                    before=before_reservation,
                    after=_snapshot_reservation_audit(source_reservation),
                    extra={'order_no': order.order_no},
                )
            pending_transfer_tasks = Transfer.objects.filter(
                order_to=order,
                status='pending'
            ).count()
            if pending_transfer_tasks > 0:
                messages.success(
                    request,
                    f'订单创建成功：{order.order_no}，已自动生成 {pending_transfer_tasks} 条转寄任务'
                )
            else:
                messages.success(
                    request,
                    f'订单创建成功：{order.order_no}，当前为仓库发货，未生成转寄任务'
                )
            return redirect('orders_list')

        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f'订单创建失败：{str(e)}')
        form_values = _extract_order_form_values(request)
    else:
        form_values = {}

    # 获取可用的SKU
    skus = SKU.objects.filter(is_active=True)
    reservation_id = (request.GET.get('reservation_id') or request.POST.get('reservation_id') or '').strip()
    if reservation_id:
        source_reservation = get_object_or_404(Reservation.objects.select_related('sku'), id=reservation_id)
        if source_reservation.can_convert:
            form_values = {
                'customer_name': source_reservation.customer_name,
                'customer_phone': source_reservation.customer_phone,
                'customer_wechat': source_reservation.customer_wechat,
                'event_date': source_reservation.event_date.strftime('%Y-%m-%d') if source_reservation.event_date else '',
                'delivery_address': source_reservation.delivery_address or '',
                'rental_days': 1,
                'order_source': 'miniprogram' if source_reservation.source == 'miniprogram' else 'wechat',
                'return_service_type': 'none',
                'return_service_fee': '0.00',
                'return_service_payment_status': 'unpaid',
                'return_pickup_status': 'not_required',
                'notes': f'来源预定单：{source_reservation.reservation_no}\n{source_reservation.notes}'.strip(),
            }
        else:
            messages.warning(request, '该预定单当前状态不可转正式订单')
            source_reservation = None

    context = {
        'skus': skus,
        'mode': 'create',
        'source_reservation': source_reservation,
        'form_values': form_values,
        **_build_order_form_meta(),
    }
    return render(request, 'orders/form.html', context)


@login_required
@require_permission('orders', 'update')
def order_edit(request, order_id):
    """编辑订单"""
    order = get_object_or_404(Order, id=order_id)
    if order.status not in ['pending', 'confirmed']:
        messages.error(request, f'订单状态为 {order.get_status_display()}，无法编辑')
        return redirect('orders_list')

    if request.method == 'POST':
        try:
            sku_ids = request.POST.getlist('sku_id[]')
            quantities = request.POST.getlist('quantity[]')
            transfer_source_order_ids = request.POST.getlist('transfer_source_order_id[]')
            items = []
            for idx, (sku_id, quantity) in enumerate(zip(sku_ids, quantities)):
                if not sku_id:
                    continue
                source_order_id = ''
                if idx < len(transfer_source_order_ids):
                    source_order_id = (transfer_source_order_ids[idx] or '').strip()
                force_warehouse = source_order_id == '__warehouse__'
                items.append({
                    'sku_id': int(sku_id),
                    'quantity': int(quantity) if quantity else 1,
                    'transfer_source_order_id': int(source_order_id) if (source_order_id and source_order_id.isdigit()) else None,
                    'force_warehouse': force_warehouse,
                })

            data = {
                'customer_name': request.POST.get('customer_name'),
                'customer_phone': request.POST.get('customer_phone'),
                'customer_wechat': request.POST.get('customer_wechat', ''),
                'xianyu_order_no': request.POST.get('xianyu_order_no', ''),
                'order_source': request.POST.get('order_source', order.order_source),
                'source_order_no': request.POST.get('source_order_no', ''),
                'customer_email': request.POST.get('customer_email', ''),
                'delivery_address': request.POST.get('delivery_address'),
                'return_address': request.POST.get('return_address', ''),
                'event_date': datetime.strptime(request.POST.get('event_date'), '%Y-%m-%d').date(),
                'rental_days': int(request.POST.get('rental_days', 1)),
                'return_service_type': request.POST.get('return_service_type', order.return_service_type),
                'return_service_fee': request.POST.get('return_service_fee', str(order.return_service_fee)),
                'return_service_payment_status': request.POST.get('return_service_payment_status', order.return_service_payment_status),
                'return_service_payment_channel': request.POST.get('return_service_payment_channel', order.return_service_payment_channel),
                'return_service_payment_reference': request.POST.get('return_service_payment_reference', order.return_service_payment_reference),
                'return_pickup_status': request.POST.get('return_pickup_status', order.return_pickup_status),
                'notes': request.POST.get('notes', ''),
                'items': items,
            }

            # 更新订单
            order = OrderService.update_order(order_id, data, request.user)
            messages.success(request, '订单更新成功')
            return redirect('orders_list')

        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f'订单更新失败：{str(e)}')
        form_values = _extract_order_form_values(request)
    else:
        form_values = {}

    skus = SKU.objects.filter(is_active=True)
    sku_preferred_source = {}
    for alloc in order.transfer_allocations_target.filter(status__in=['locked', 'consumed']).order_by('created_at'):
        sku_preferred_source.setdefault(alloc.sku_id, alloc.source_order_id)
    order_items_with_transfer = []
    for item in order.items.all():
        order_items_with_transfer.append({
            'item': item,
            'preferred_source_order_id': sku_preferred_source.get(item.sku_id),
        })

    context = {
        'order': order,
        'skus': skus,
        'mode': 'edit',
        'order_items_with_transfer': order_items_with_transfer,
        'form_values': form_values,
        **_build_order_form_meta(),
    }
    return render(request, 'orders/form.html', context)


@login_required
@require_permission('orders', 'view')
def order_detail(request, order_id):
    """订单详情"""
    order = get_object_or_404(
        Order.objects.select_related('created_by').prefetch_related('items__sku'),
        id=order_id
    )

    allocations_qs = order.transfer_allocations_target.select_related('source_order', 'sku').order_by('sku_id', 'source_order__event_date', 'source_order__order_no')
    transfer_allocations_by_sku = defaultdict(list)
    for alloc in allocations_qs:
        transfer_allocations_by_sku[alloc.sku_id].append(alloc)
    item_rows = []
    expected_deposit_total = Decimal('0.00')
    for item in order.items.all():
        expected_deposit_total += (item.deposit or Decimal('0.00')) * int(item.quantity or 0)
        item_rows.append({
            'item': item,
            'allocations': transfer_allocations_by_sku.get(item.sku_id, []),
        })
    finance_transactions = list(
        order.finance_transactions.select_related('created_by').order_by('-created_at', '-id')
    )
    order.can_mark_returned_in_orders_center = not _is_transfer_source_order_active(order)
    order.active_as_source_count = TransferAllocation.objects.filter(
        source_order=order,
        status__in=['locked', 'consumed'],
        target_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
    ).count()

    context = {
        'order': order,
        'transfer_allocations_by_sku': dict(transfer_allocations_by_sku),
        'target_transfer_allocations': list(allocations_qs),
        'item_rows': item_rows,
        'finance_transactions': finance_transactions,
        'expected_deposit_total': expected_deposit_total,
        'order_source_label': dict(Order.ORDER_SOURCE_CHOICES).get(order.order_source, '-'),
        'return_service_type_label': dict(Order.RETURN_SERVICE_TYPE_CHOICES).get(order.return_service_type, '-'),
        'return_service_payment_status_label': dict(Order.RETURN_SERVICE_PAYMENT_STATUS_CHOICES).get(order.return_service_payment_status, '-'),
        'return_service_payment_channel_label': dict(Order.RETURN_SERVICE_PAYMENT_CHANNEL_CHOICES).get(order.return_service_payment_channel, '-') if order.return_service_payment_channel else '-',
        'return_pickup_status_label': dict(Order.RETURN_PICKUP_STATUS_CHOICES).get(order.return_pickup_status, '-'),
    }
    return render(request, 'orders/detail.html', context)


@login_required
@require_permission('orders', 'update')
def order_finance_add(request, order_id):
    """订单详情：手工新增财务流水"""
    if request.method == 'POST':
        if not has_action_permission(request.user, 'finance.manual_adjust'):
            messages.error(request, '您没有执行此操作的权限（finance.manual_adjust）')
            return redirect('order_detail', order_id=order_id)
        try:
            order = get_object_or_404(Order, id=order_id)
            tx_type = (request.POST.get('transaction_type') or '').strip()
            amount = Decimal(request.POST.get('amount', '0') or '0')
            notes = (request.POST.get('notes') or '').strip()
            tx = OrderService.record_manual_finance(
                order=order,
                transaction_type=tx_type,
                amount=amount,
                user=request.user,
                notes=notes or '手工记账',
            )
            AuditService.log_with_diff(
                user=request.user,
                action='create',
                module='财务',
                target=f'{order.order_no}#{tx.id}',
                summary='手工新增财务流水',
                before={},
                after={
                    'order_no': order.order_no,
                    'transaction_type': tx.transaction_type,
                    'amount': str(tx.amount),
                    'notes': tx.notes,
                },
            )
            messages.success(request, '已新增财务流水')
        except Exception as e:
            messages.error(request, f'新增失败：{str(e)}')
    return redirect('order_detail', order_id=order_id)


@login_required
@require_permission('orders', 'update')
def order_return_service_update(request, order_id):
    """订单详情：独立登记/修改包回邮服务，适配发货后补录场景"""
    if request.method != 'POST':
        return redirect('order_detail', order_id=order_id)
    try:
        OrderService.update_return_service(
            order_id,
            {
                'return_service_type': request.POST.get('return_service_type', 'none'),
                'return_service_fee': request.POST.get('return_service_fee', '0'),
                'return_service_payment_status': request.POST.get('return_service_payment_status', 'unpaid'),
                'return_service_payment_channel': request.POST.get('return_service_payment_channel', ''),
                'return_service_payment_reference': request.POST.get('return_service_payment_reference', ''),
                'return_pickup_status': request.POST.get('return_pickup_status', 'not_required'),
            },
            request.user,
        )
        messages.success(request, '包回邮服务信息更新成功')
    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        messages.error(request, f'包回邮服务更新失败：{exc}')
    return redirect('order_detail', order_id=order_id)


@login_required
@require_permission('orders', 'delete')
def order_delete(request, order_id):
    """删除订单"""
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)

        # 只有待处理状态的订单可以删除
        if order.status != 'pending':
            messages.error(request, '只有待处理状态的订单可以删除')
            return redirect('orders_list')

        order_no = order.order_no
        order.delete()
        messages.success(request, f'订单 {order_no} 已删除')
        return redirect('orders_list')

    return redirect('orders_list')


@login_required
@require_permission('orders', 'delete')
def orders_bulk_delete(request):
    """批量删除订单（仅待处理）"""
    if request.method != 'POST':
        return redirect('orders_list')
    ids = request.POST.getlist('ids[]') or request.POST.getlist('ids')
    order_ids = [int(x) for x in ids if str(x).isdigit()]
    if not order_ids:
        messages.error(request, '请选择要删除的订单')
        return redirect('orders_list')
    qs = Order.objects.filter(id__in=order_ids)
    deleted = 0
    skipped = 0
    for order in qs:
        if order.status != 'pending':
            skipped += 1
            continue
        order.delete()
        deleted += 1
    if deleted:
        messages.success(request, f'批量删除完成：成功 {deleted} 条')
    if skipped:
        messages.warning(request, f'有 {skipped} 条因状态非待处理未删除')
    return redirect('orders_list')


@login_required
def calendar_view(request):
    """历史日历入口兼容：功能已下线，统一回工作台"""
    messages.info(request, '日历排期功能已下线，请改用订单中心、预定管理和转寄中心处理业务。')
    return redirect('dashboard')


@login_required
@require_permission('transfers', 'view')
def transfers_list(request):
    """转寄中心"""
    transfer_complete_feedback = request.session.pop('transfer_complete_feedback', None)
    candidates = build_transfer_pool_rows()
    keyword = (request.GET.get('keyword', '') or '').strip()
    status_filter = (request.GET.get('status', 'pending') or 'pending').strip()
    candidate_action_filter = (request.GET.get('candidate_action', 'all') or 'all').strip()
    active_panel = (request.GET.get('panel', 'candidates') or 'candidates').strip()
    if status_filter not in ['pending', 'completed', 'cancelled', 'all']:
        status_filter = 'pending'
    if candidate_action_filter not in ['all', 'generatable']:
        candidate_action_filter = 'all'
    if active_panel not in ['candidates', 'tasks']:
        active_panel = 'candidates'

    # 获取转寄任务
    tasks = Transfer.objects.select_related(
        'order_from', 'order_to', 'sku'
    ).order_by('-created_at')
    if status_filter and status_filter != 'all':
        tasks = tasks.filter(status=status_filter)
    if keyword:
        keyword_lower = keyword.lower()
        keyword_id = int(keyword) if keyword.isdigit() else None
        task_q = (
            Q(order_from__order_no__icontains=keyword) |
            Q(order_to__order_no__icontains=keyword) |
            Q(sku__name__icontains=keyword) |
            Q(order_from__customer_name__icontains=keyword) |
            Q(order_to__customer_name__icontains=keyword) |
            Q(order_from__customer_phone__icontains=keyword) |
            Q(order_to__customer_phone__icontains=keyword)
        )
        if keyword_id:
            task_q |= Q(id=keyword_id)
        tasks = tasks.filter(task_q)
        candidates = [
            c for c in candidates
            if keyword_lower in (c['order'].order_no or '').lower()
            or keyword_lower in (c['item'].sku.name or '').lower()
            or keyword_lower in (c['order'].customer_name or '').lower()
            or keyword_lower in (c['order'].customer_phone or '').lower()
            or keyword_lower in (c['current_source_text'] or '').lower()
            or keyword_lower in (c['recommended_source_text'] or '').lower()
        ]
    if candidate_action_filter == 'generatable':
        candidates = [c for c in candidates if c.get('can_generate_task')]

    candidates_page = Paginator(candidates, _get_page_size('page_size_transfer_candidates', 5)).get_page(request.GET.get('candidate_page'))
    tasks_page = Paginator(tasks, _get_page_size('page_size_transfer_tasks')).get_page(request.GET.get('task_page'))

    task_ids = [t.id for t in tasks_page.object_list]
    completed_unit_map = defaultdict(list)
    if task_ids:
        completed_moves = UnitMovement.objects.filter(
            transfer_id__in=task_ids,
            event_type='TRANSFER_COMPLETED'
        ).select_related('unit').order_by('transfer_id', 'unit__unit_no')
        for mv in completed_moves:
            if mv.unit and mv.unit.unit_no not in completed_unit_map[mv.transfer_id]:
                completed_unit_map[mv.transfer_id].append(mv.unit.unit_no)

    pending_source_keys = {(t.order_from_id, t.sku_id) for t in tasks_page.object_list if t.status == 'pending'}
    pending_source_map = defaultdict(list)
    if pending_source_keys:
        order_ids = [k[0] for k in pending_source_keys]
        sku_ids = [k[1] for k in pending_source_keys]
        for row in InventoryUnit.objects.filter(
            current_order_id__in=order_ids,
            sku_id__in=sku_ids,
            is_active=True,
        ).values('current_order_id', 'sku_id', 'unit_no').order_by('current_order_id', 'sku_id', 'unit_no'):
            pending_source_map[(row['current_order_id'], row['sku_id'])].append(row['unit_no'])

    for task in tasks_page.object_list:
        task.transfer_ship_date = task.order_from.event_date + timedelta(days=1)
        sku_name = ''
        if getattr(task, 'sku', None):
            sku_name = (task.sku.name or '').strip()
        if not sku_name and task.sku_id:
            fallback_item = OrderItem.objects.filter(order_id=task.order_to_id, sku_id=task.sku_id).select_related('sku').first()
            if fallback_item and fallback_item.sku:
                sku_name = (fallback_item.sku.name or '').strip()
        task.style_display = f"{sku_name or ('SKU#' + str(task.sku_id))} x {task.quantity}"
        if task.status == 'completed':
            task.unit_nos = completed_unit_map.get(task.id, [])
        elif task.status == 'pending':
            source_units = pending_source_map.get((task.order_from_id, task.sku_id), [])
            task.unit_nos = source_units[: max(int(task.quantity or 0), 0)]
        else:
            task.unit_nos = []
        if task.unit_nos:
            more = '...' if len(task.unit_nos) > 5 else ''
            task.unit_nos_display = '、'.join(task.unit_nos[:5]) + more
        else:
            task.unit_nos_display = '-'
        chain_lines = []
        chain_rows = []
        for unit_no in task.unit_nos[:2]:
            unit = InventoryUnit.objects.filter(unit_no=unit_no, is_active=True).first()
            if not unit:
                continue
            moves = UnitMovement.objects.filter(unit=unit).select_related('from_order', 'to_order').order_by('event_time')
            chain = []
            event_rows = []
            for mv in moves:
                label = dict(UnitMovement.EVENT_CHOICES).get(mv.event_type, mv.event_type)
                if mv.from_order and mv.to_order:
                    chain.append(f"{label}({mv.from_order.order_no}->{mv.to_order.order_no})")
                elif mv.to_order:
                    chain.append(f"{label}(->{mv.to_order.order_no})")
                elif mv.from_order:
                    chain.append(f"{label}({mv.from_order.order_no}->)")
                else:
                    chain.append(label)
                event_rows.append({
                    'time': mv.event_time.strftime('%Y-%m-%d %H:%M'),
                    'event': label,
                    'from_order': mv.from_order.order_no if mv.from_order else '-',
                    'to_order': mv.to_order.order_no if mv.to_order else '-',
                    'tracking_no': mv.tracking_no or '-',
                    'status': dict(UnitMovement.STATUS_CHOICES).get(mv.status, mv.status),
                })
            if chain:
                chain_lines.append(f"{unit_no}: " + " -> ".join(chain))
            chain_rows.append({
                'unit_no': unit_no,
                'events': event_rows,
            })
        task.unit_chain_text = "\n".join(chain_lines) if chain_lines else "暂无链路数据"
        task.unit_chain_json = json.dumps(chain_rows, ensure_ascii=False)

    context = {
        'candidates': candidates_page,
        'tasks': tasks_page,
        'candidates_page': candidates_page,
        'tasks_page': tasks_page,
        'keyword': keyword,
        'status_filter': status_filter,
        'candidate_action_filter': candidate_action_filter,
        'active_panel': active_panel,
        'candidate_pagination_query': _build_querystring(request, ['candidate_page']),
        'task_pagination_query': _build_querystring(request, ['task_page']),
        'task_tab_query': _build_querystring(request, ['status', 'task_page']),
        'transfer_complete_feedback_json': json.dumps(transfer_complete_feedback, ensure_ascii=False) if transfer_complete_feedback else '',
    }
    return render(request, 'transfers.html', context)


@login_required
@require_permission('transfers', 'view')
def transfer_recommendation_logs(request):
    """转寄推荐回放列表"""
    logs = TransferRecommendationLog.objects.select_related('order', 'sku', 'operator').all()
    keyword = (request.GET.get('keyword', '') or '').strip()
    trigger_type = (request.GET.get('trigger_type', '') or '').strip()
    decision_type = (request.GET.get('decision_type', '') or '').strip()
    export_flag = (request.GET.get('export') or '').strip() == '1'
    if keyword:
        logs = logs.filter(
            Q(order__order_no__icontains=keyword) |
            Q(order__customer_name__icontains=keyword) |
            Q(sku__code__icontains=keyword) |
            Q(sku__name__icontains=keyword)
        )
    if trigger_type in ['recommend', 'create', 'manual']:
        logs = logs.filter(trigger_type=trigger_type)
    if decision_type == 'transfer':
        logs = logs.filter(selected_source_order_id__isnull=False)
    elif decision_type == 'warehouse':
        logs = logs.filter(Q(selected_source_order_id__isnull=True) | Q(selected_source_order_id=0))

    logs = logs.order_by('-created_at', '-id')
    if export_flag:
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = 'attachment; filename="transfer_recommendation_logs.csv"'
        writer = csv.writer(response)
        writer.writerow([
            '时间', '目标订单号', '目标客户', 'SKU编码', 'SKU名称', '触发类型',
            '推荐前来源单ID', '推荐后来源单号', '仓库补量', '候选数', '命中排名', '命中总分', '决策说明', '操作人'
        ])
        for log in logs:
            summary = log.score_summary or {}
            writer.writerow([
                log.created_at.strftime('%Y-%m-%d %H:%M:%S') if log.created_at else '',
                log.order.order_no if log.order else '',
                log.order.customer_name if log.order else '',
                log.sku.code if log.sku else '',
                log.sku.name if log.sku else '',
                log.get_trigger_type_display(),
                ','.join(str(i) for i in (log.before_source_order_ids or [])),
                log.selected_source_order_no or '',
                int(log.warehouse_needed or 0),
                int(summary.get('candidate_count', 0)),
                summary.get('selected_rank') or '',
                summary.get('selected_score_total') or '',
                summary.get('decision_reason') or '',
                (log.operator.full_name if log.operator and log.operator.full_name else (log.operator.username if log.operator else '')),
            ])
        return response
    logs_page = Paginator(logs, _get_page_size('page_size_transfer_logs')).get_page(request.GET.get('page'))
    context = {
        'logs': logs_page,
        'logs_page': logs_page,
        'keyword': keyword,
        'trigger_type': trigger_type,
        'decision_type': decision_type,
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'transfer_recommendation_logs.html', context)


@login_required
@require_permission('transfers', 'update')
def transfer_recommend(request):
    """批量/单条重新推荐转寄来源"""
    if request.method != 'POST':
        return redirect('transfers_list')
    if not has_action_permission(request.user, 'transfer.recommend'):
        messages.error(request, '您没有执行此操作的权限（transfer.recommend）')
        return redirect('transfers_list')
    rows = request.POST.getlist('rows[]') or request.POST.getlist('rows')
    if not rows:
        messages.error(request, '请先选择候选项')
        return redirect('transfers_list')

    success = 0
    skipped_pending_task = 0
    skipped_invalid = 0

    for row in rows:
        if ':' not in row:
            skipped_invalid += 1
            continue
        order_id_raw, sku_id_raw = row.split(':', 1)
        if not order_id_raw.isdigit() or not sku_id_raw.isdigit():
            skipped_invalid += 1
            continue
        order_id = int(order_id_raw)
        sku_id = int(sku_id_raw)

        order = Order.objects.filter(id=order_id, status__in=['pending', 'confirmed', 'delivered']).first()
        if not order:
            skipped_invalid += 1
            continue
        item = OrderItem.objects.filter(order=order, sku_id=sku_id).first()
        if not item:
            skipped_invalid += 1
            continue
        if Transfer.objects.filter(order_to=order, sku_id=sku_id, status__in=['pending', 'completed']).exists():
            skipped_pending_task += 1
            continue

        before_source_ids = list(
            TransferAllocation.objects.filter(
                target_order=order,
                sku_id=sku_id,
                status='locked',
            ).values_list('source_order_id', flat=True)
        )

        TransferAllocation.objects.filter(
            target_order=order,
            sku_id=sku_id,
            status='locked'
        ).update(status='released')

        plan = build_transfer_allocation_plan(
            delivery_address=order.delivery_address,
            target_event_date=order.event_date,
            sku_id=sku_id,
            quantity=item.quantity,
            exclude_target_order_id=order.id,
        )

        for alloc in plan.get('allocations', []):
            TransferAllocation.objects.create(
                source_order_id=alloc['source_order_id'],
                target_order=order,
                sku_id=alloc['sku_id'],
                quantity=alloc['quantity'],
                target_event_date=alloc['target_event_date'],
                window_start=alloc['window_start'],
                window_end=alloc['window_end'],
                distance_score=alloc['distance_score'],
                status='locked',
                created_by=request.user,
            )

        alloc_text = ', '.join([f"{a['source_order_no']} x{a['quantity']}" for a in plan.get('allocations', [])]) or '仓库发货'
        _log_transfer_action(
            request.user,
            'update',
            order.order_no,
            f'重新推荐挂靠：SKU#{sku_id} -> {alloc_text}'
        )
        after_source_ids = list(
            TransferAllocation.objects.filter(
                target_order=order,
                sku_id=sku_id,
                status='locked',
            ).values_list('source_order_id', flat=True)
        )
        if order.status == 'delivered' and before_source_ids != after_source_ids:
            RiskEventService.create_event(
                event_type='delivered_recommend_change',
                level='high',
                module='转寄',
                title='已发货订单重新调整挂靠来源',
                description=f'订单 {order.order_no}（SKU#{sku_id}）挂靠来源发生变更',
                event_data={
                    'order_id': order.id,
                    'order_no': order.order_no,
                    'sku_id': sku_id,
                    'before_source_ids': before_source_ids,
                    'after_source_ids': after_source_ids,
                },
                order=order,
                detected_by=request.user,
            )
        _save_transfer_recommendation_log(
            order=order,
            sku_id=sku_id,
            before_source_ids=before_source_ids,
            plan=plan,
            operator=request.user,
            trigger_type='recommend',
        )
        success += 1

    if success:
        messages.success(request, f'重新推荐完成：成功 {success} 条（仅更新挂靠，不生成任务）')
    if skipped_pending_task:
        messages.warning(request, f'跳过 {skipped_pending_task} 条：已存在转寄任务（待执行或已完成），不可重推')
    if skipped_invalid:
        messages.warning(request, f'跳过 {skipped_invalid} 条：数据无效或状态不允许')
    return redirect('transfers_list')


@login_required
@require_permission('transfers', 'create')
def transfer_generate_tasks(request):
    """批量/单条生成转寄任务（与重新推荐分离）"""
    if request.method != 'POST':
        return redirect('transfers_list')
    if not has_action_permission(request.user, 'transfer.create_task'):
        messages.error(request, '您没有执行此操作的权限（transfer.create_task）')
        return redirect('transfers_list')
    rows = request.POST.getlist('rows[]') or request.POST.getlist('rows')
    if not rows:
        messages.error(request, '请先选择候选项')
        return redirect('transfers_list')

    success = 0
    skipped_warehouse = 0
    skipped_not_delivered = 0
    skipped_exists = 0
    skipped_invalid = 0
    updated_to_recommended = 0

    for row in rows:
        if ':' not in row:
            skipped_invalid += 1
            continue
        order_id_raw, sku_id_raw = row.split(':', 1)
        if not order_id_raw.isdigit() or not sku_id_raw.isdigit():
            skipped_invalid += 1
            continue
        order_id = int(order_id_raw)
        sku_id = int(sku_id_raw)

        order = Order.objects.filter(id=order_id).first()
        if not order or not OrderItem.objects.filter(order=order, sku_id=sku_id).exists():
            skipped_invalid += 1
            continue
        if order.status != 'delivered':
            skipped_not_delivered += 1
            continue
        if Transfer.objects.filter(order_to=order, sku_id=sku_id, status__in=['pending', 'completed']).exists():
            skipped_exists += 1
            continue

        # 生成任务前，若推荐来源与当前挂靠不一致，先更新挂靠到推荐来源
        current_locked = list(
            TransferAllocation.objects.filter(
                target_order=order,
                sku_id=sku_id,
                status='locked'
            ).order_by('created_at')
        )
        current_source_id = current_locked[0].source_order_id if current_locked else None
        match_candidates = get_transfer_match_candidates(
            order.delivery_address,
            order.event_date,
            sku_id,
            exclude_target_order_id=order.id,
        )
        recommended_source_id = match_candidates[0]['source_order'].id if match_candidates else None

        if recommended_source_id and current_source_id != recommended_source_id:
            TransferAllocation.objects.filter(
                target_order=order,
                sku_id=sku_id,
                status='locked'
            ).update(status='released')
            item = OrderItem.objects.filter(order=order, sku_id=sku_id).first()
            plan = build_transfer_allocation_plan(
                delivery_address=order.delivery_address,
                target_event_date=order.event_date,
                sku_id=sku_id,
                quantity=(item.quantity if item else 1),
                preferred_source_order_id=recommended_source_id,
                exclude_target_order_id=order.id,
            )
            for alloc in plan.get('allocations', []):
                TransferAllocation.objects.create(
                    source_order_id=alloc['source_order_id'],
                    target_order=order,
                    sku_id=alloc['sku_id'],
                    quantity=alloc['quantity'],
                    target_event_date=alloc['target_event_date'],
                    window_start=alloc['window_start'],
                    window_end=alloc['window_end'],
                    distance_score=alloc['distance_score'],
                    status='locked',
                    created_by=request.user,
                )
            updated_to_recommended += 1
            _log_transfer_action(
                request.user,
                'update',
                order.order_no,
                f'生成任务前自动切换挂靠：SKU#{sku_id} {current_source_id or "仓库"} -> {recommended_source_id}'
            )

        if not TransferAllocation.objects.filter(
            target_order=order,
            sku_id=sku_id,
            status='locked'
        ).exists():
            skipped_warehouse += 1
            continue

        sync_transfer_tasks_for_target_order(order, request.user, sku_id=sku_id)
        for t in Transfer.objects.filter(order_to=order, sku_id=sku_id, status='pending').select_related('order_from', 'sku'):
            _log_transfer_action(
                request.user,
                'create',
                f'任务#{t.id}',
                f'生成转寄任务：{t.order_from.order_no} -> {t.order_to.order_no}，SKU={t.sku.name}，数量={t.quantity}'
            )
        success += 1

    if success:
        messages.success(request, f'生成转寄任务成功：{success} 条')
    if updated_to_recommended:
        messages.info(request, f'其中 {updated_to_recommended} 条已先更新为推荐来源挂靠')
    if skipped_warehouse:
        messages.warning(request, f'跳过 {skipped_warehouse} 条：当前挂靠为仓库发货')
    if skipped_not_delivered:
        messages.warning(request, f'跳过 {skipped_not_delivered} 条：仅“已发货”订单可生成转寄任务')
    if skipped_exists:
        messages.warning(request, f'跳过 {skipped_exists} 条：已存在转寄任务（待执行或已完成）')
    if skipped_invalid:
        messages.warning(request, f'跳过 {skipped_invalid} 条：数据无效或状态不允许')
    return redirect('transfers_list')


@login_required
@require_permission('transfers', 'create')
def transfer_create(request):
    """创建转寄任务"""
    if request.method == 'POST':
        if not has_action_permission(request.user, 'transfer.create_task'):
            messages.error(request, '您没有执行此操作的权限（transfer.create_task）')
            return redirect('transfers_list')
        try:
            order_from_id = int(request.POST.get('order_from_id'))
            order_to_id = int(request.POST.get('order_to_id'))
            sku_id = int(request.POST.get('sku_id'))
            transfer = create_transfer_task(order_from_id, order_to_id, sku_id, request.user)
            _log_transfer_action(
                request.user,
                'create',
                f'任务#{transfer.id}',
                f'手动创建转寄任务：{transfer.order_from.order_no} -> {transfer.order_to.order_no}，SKU={transfer.sku.name}，数量={transfer.quantity}'
            )
            messages.success(request, '转寄任务创建成功')
        except Exception as e:
            messages.error(request, f'转寄任务创建失败：{str(e)}')
    return redirect('transfers_list')


@login_required
@require_permission('transfers', 'update')
def transfer_complete(request, transfer_id):
    """完成转寄任务"""
    if request.method == 'POST':
        if not has_action_permission(request.user, 'transfer.complete_task'):
            request.session['transfer_complete_feedback'] = {
                'status': 'error',
                'title': '操作失败',
                'message': '您没有执行此操作的权限（transfer.complete_task）',
            }
            return redirect('transfers_list')
        try:
            with transaction.atomic():
                transfer = get_object_or_404(Transfer.objects.select_related('order_from', 'order_to'), id=transfer_id)
                if transfer.status != 'pending':
                    raise ValueError('仅待执行任务可完成')
                before_transfer = _snapshot_transfer_audit(transfer)

                tracking_no = (request.POST.get('tracking_no') or '').strip()
                if not tracking_no:
                    # 兼容旧字段，若前端仍传两字段则兜底使用
                    tracking_no = (request.POST.get('target_ship_tracking') or '').strip() or (
                        request.POST.get('source_return_tracking') or ''
                    ).strip()
                if not tracking_no:
                    raise ValueError('请录入快递单号')

                target_order = transfer.order_to
                source_order = transfer.order_from
                before_target_order = _snapshot_order_audit(target_order)
                before_source_order = _snapshot_order_audit(source_order)
                source_completed_now = False

                # 1) 新单：写入发货单号并推进至已发货
                target_order.ship_tracking = tracking_no
                if target_order.status != 'delivered':
                    target_order.status = 'delivered'
                target_order.save(update_fields=['ship_tracking', 'status', 'updated_at'])

                consumed_qty, shortfall_qty = _consume_transfer_allocations_for_transfer(
                    transfer,
                    operator=request.user,
                )

                # 2) 来源单：写入回收单号并推进至已完成（归还 -> 完成）
                source_order.return_tracking = tracking_no
                if source_order.status in ['delivered', 'in_use']:
                    source_order.status = 'returned'
                    source_order.save(update_fields=['return_tracking', 'status', 'updated_at'])
                    source_order.status = 'completed'
                    source_order.save(update_fields=['status', 'updated_at'])
                    source_completed_now = True
                elif source_order.status == 'returned':
                    source_order.save(update_fields=['return_tracking', 'updated_at'])
                    source_order.status = 'completed'
                    source_order.save(update_fields=['status', 'updated_at'])
                    source_completed_now = True
                elif source_order.status == 'completed':
                    source_order.save(update_fields=['return_tracking', 'updated_at'])
                else:
                    raise ValueError(f'来源单状态为 {source_order.get_status_display()}，无法执行归还完成')
                if source_completed_now:
                    OrderService.record_deposit_refund(source_order, request.user, notes='转寄闭环完成后退还来源单押金')

                InventoryUnitService.transfer_to_target(
                    source_order=source_order,
                    target_order=target_order,
                    sku=transfer.sku,
                    quantity=int(transfer.quantity or 0),
                    tracking_no=tracking_no,
                    transfer=transfer,
                    operator=request.user,
                )

                transfer.status = 'completed'
                transfer.notes = (transfer.notes + '\n' if transfer.notes else '') + (
                    f'完成闭环：快递单号={tracking_no}'
                )
                if shortfall_qty > 0:
                    transfer.notes += f'；分配锁不足{shortfall_qty}'
                transfer.save(update_fields=['status', 'notes', 'updated_at'])

                AuditService.log_with_diff(
                    user=request.user,
                    action='status_change',
                    module='转寄',
                    target=f'任务#{transfer.id}',
                    summary='标记转寄任务完成',
                    before={
                        'transfer': before_transfer,
                        'target_order': before_target_order,
                        'source_order': before_source_order,
                    },
                    after={
                        'transfer': _snapshot_transfer_audit(transfer),
                        'target_order': _snapshot_order_audit(target_order),
                        'source_order': _snapshot_order_audit(source_order),
                    },
                    extra={
                        'tracking_no': tracking_no,
                        'allocation_consumed_qty': consumed_qty,
                        'allocation_shortfall_qty': shortfall_qty,
                    },
                )
            message = '转寄任务已完成'
            if shortfall_qty > 0:
                message += f'\n挂靠锁不足 {shortfall_qty} 套，请检查历史数据。'
            request.session['transfer_complete_feedback'] = {
                'status': 'success',
                'title': '完成成功',
                'message': message,
            }
        except Exception as e:
            request.session['transfer_complete_feedback'] = {
                'status': 'error',
                'title': '操作失败',
                'message': str(e),
            }
    return redirect('transfers_list')


@login_required
@require_permission('transfers', 'update')
def transfer_cancel(request, transfer_id):
    """取消转寄任务"""
    if request.method == 'POST':
        try:
            transfer = get_object_or_404(Transfer, id=transfer_id)
            reason = (request.POST.get('reason') or '手动取消').strip() or '手动取消'
            if has_action_permission(request.user, 'transfer.cancel_task'):
                _execute_transfer_cancel(transfer, request.user, reason)
                messages.success(request, '转寄任务已取消')
            elif can_request_approval(request.user):
                _request_high_risk_approval(
                    request,
                    action_code='transfer.cancel_task',
                    module='转寄',
                    target_type='transfer',
                    target_id=transfer.id,
                    target_label=f'任务#{transfer.id}',
                    summary=f'申请取消转寄任务 #{transfer.id}',
                    payload={
                        'transfer_id': transfer.id,
                        'reason': reason,
                    },
                )
            else:
                messages.error(request, '您没有执行此操作的权限（transfer.cancel_task）')
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('transfers_list')


@login_required
@require_permission('outbound_inventory', 'view')
def outbound_inventory_dashboard(request):
    """在外库存看板（系统级）"""
    if request.GET.get('init_units') == '1':
        if not has_action_permission(request.user, 'inventory.init_units'):
            messages.error(request, '您没有执行此操作的权限（inventory.init_units）')
            return redirect('outbound_inventory_dashboard')
        created = InventoryUnitService.bootstrap_all_units()
        AuditService.log_with_diff(
            user=request.user,
            action='create' if created else 'update',
            module='在外库存',
            target='inventory_units.bootstrap',
            summary='初始化单套库存编号',
            before={},
            after={'created_units': int(created)},
            extra={'result': 'created' if created else 'noop'},
        )
        if created:
            messages.success(request, f'已初始化单套库存：新增 {created} 条')
        else:
            messages.info(request, '单套库存已是最新，无需初始化')
        return redirect('outbound_inventory_dashboard')

    settings = get_system_settings()
    warn_outbound_days = int(settings.get('outbound_max_days_warn', 10) or 10)
    warn_hops = int(settings.get('outbound_max_hops_warn', 4) or 4)
    pending_timeout_hours = int(settings.get('transfer_pending_timeout_hours', 24) or 24)
    shipped_timeout_days = int(settings.get('transfer_shipped_timeout_days', 3) or 3)

    keyword = (request.GET.get('keyword') or '').strip()
    sku_id = (request.GET.get('sku_id') or '').strip()
    status = (request.GET.get('status') or '').strip()
    event_type = (request.GET.get('event_type') or '').strip()
    anomaly_only = (request.GET.get('anomaly_only') or '').strip() == '1'
    part_issue_only = (request.GET.get('part_issue_only') or '').strip() == '1'
    topology_sku_id = (request.GET.get('topology_sku_id') or '').strip()
    topology_unit_no = (request.GET.get('topology_unit_no') or '').strip()

    units = InventoryUnit.objects.select_related('sku', 'current_order').filter(is_active=True).order_by('sku__code', 'unit_no')
    if sku_id.isdigit():
        units = units.filter(sku_id=int(sku_id))
    if status:
        units = units.filter(status=status)
    if keyword:
        units = units.filter(
            Q(unit_no__icontains=keyword)
            | Q(sku__code__icontains=keyword)
            | Q(sku__name__icontains=keyword)
            | Q(current_order__order_no__icontains=keyword)
            | Q(last_tracking_no__icontains=keyword)
        )

    # 全量最新节点（用于总览、SKU表、预警）
    all_active_units = list(InventoryUnit.objects.filter(is_active=True).values('id', 'sku_id', 'status'))
    all_active_ids = [u['id'] for u in all_active_units]
    status_map = {u['id']: u['status'] for u in all_active_units}
    latest_by_unit = {}
    if all_active_ids:
        for mv in UnitMovement.objects.filter(unit_id__in=all_active_ids).order_by('unit_id', '-event_time'):
            if mv.unit_id not in latest_by_unit:
                latest_by_unit[mv.unit_id] = mv

    now = timezone.now()
    today = now.date()

    # 业务总览卡片
    outbound_total = InventoryUnit.objects.filter(is_active=True).exclude(status='in_warehouse').count()
    transfer_in_transit = 0
    exception_unit_ids = set()
    part_issue_by_unit = {
        row['unit_id']: int(row['cnt'] or 0)
        for row in InventoryUnitPart.objects.filter(
            unit_id__in=all_active_ids,
            is_active=True,
            status__in=['missing', 'damaged', 'lost']
        ).values('unit_id').annotate(cnt=Count('id'))
    }
    for unit_id, mv in latest_by_unit.items():
        if mv.event_type in ['TRANSFER_PENDING', 'TRANSFER_SHIPPED']:
            transfer_in_transit += 1
        if mv.status in ['warning', 'timeout'] or mv.event_type == 'EXCEPTION':
            exception_unit_ids.add(unit_id)
    for unit_id, cnt in part_issue_by_unit.items():
        if int(cnt or 0) > 0:
            exception_unit_ids.add(unit_id)
    today_out = UnitMovement.objects.filter(event_type='WAREHOUSE_OUT', event_time__date=today).count()
    today_returned = UnitMovement.objects.filter(event_type='RETURNED_WAREHOUSE', event_time__date=today).count()

    summary = {
        'outbound_total': outbound_total,
        'transfer_in_transit': transfer_in_transit,
        'today_out': today_out,
        'today_returned': today_returned,
        'exception_nodes': len(exception_unit_ids),
    }

    hop_counts = {
        row['unit_id']: int(row['cnt'] or 0)
        for row in UnitMovement.objects.filter(
            unit_id__in=all_active_ids,
            event_type__in=['TRANSFER_SHIPPED', 'TRANSFER_COMPLETED']
        ).values('unit_id').annotate(cnt=Count('id'))
    }
    warn_reason_by_unit = {}
    for unit_id in all_active_ids:
        warn_reason = ''
        unit_status = status_map.get(unit_id)
        latest_mv = latest_by_unit.get(unit_id)
        unit_hops = hop_counts.get(unit_id, 0)
        if unit_hops > warn_hops:
            warn_reason = f'转寄节点>{warn_hops}'
        if latest_mv and unit_status != 'in_warehouse':
            outbound_days = (now - latest_mv.event_time).days
            if outbound_days > warn_outbound_days:
                warn_reason = (warn_reason + '；' if warn_reason else '') + f'在途>{warn_outbound_days}天'
            if latest_mv.event_type == 'TRANSFER_PENDING' and (now - latest_mv.event_time).total_seconds() > pending_timeout_hours * 3600:
                warn_reason = (warn_reason + '；' if warn_reason else '') + f'待执行>{pending_timeout_hours}小时'
            if latest_mv.event_type == 'TRANSFER_SHIPPED' and (now - latest_mv.event_time).days > shipped_timeout_days:
                warn_reason = (warn_reason + '；' if warn_reason else '') + f'转寄在途>{shipped_timeout_days}天'
        elif latest_mv and latest_mv.event_type == 'EXCEPTION':
            warn_reason = (warn_reason + '；' if warn_reason else '') + '异常节点'
        part_issue_cnt = int(part_issue_by_unit.get(unit_id, 0) or 0)
        if part_issue_cnt > 0:
            warn_reason = (warn_reason + '；' if warn_reason else '') + f'部件异常{part_issue_cnt}项'
        if warn_reason:
            warn_reason_by_unit[unit_id] = warn_reason

    # 在外平均健康分（仅统计非在库）
    outbound_health_scores = []
    for unit_id in all_active_ids:
        unit_status = status_map.get(unit_id)
        if unit_status == 'in_warehouse':
            continue
        latest_mv = latest_by_unit.get(unit_id)
        hop_count = hop_counts.get(unit_id, 0)
        outbound_days = (now - latest_mv.event_time).days if latest_mv else 0
        warn_reason = warn_reason_by_unit.get(unit_id, '')
        score, _ = _compute_unit_health(
            unit_status=unit_status,
            hop_count=hop_count,
            outbound_days=outbound_days,
            warn_reason=warn_reason,
            latest_event_type=(latest_mv.event_type if latest_mv else ''),
        )
        outbound_health_scores.append(score)
    summary['avg_outbound_health'] = round(sum(outbound_health_scores) / len(outbound_health_scores), 1) if outbound_health_scores else 100.0

    if anomaly_only:
        units = units.filter(id__in=list(warn_reason_by_unit.keys()))
    if part_issue_only:
        units = units.filter(id__in=[uid for uid, cnt in part_issue_by_unit.items() if int(cnt or 0) > 0])

    # SKU总表增强：最长在途天数/转寄在途/异常数
    unit_brief = all_active_units
    by_sku = defaultdict(list)
    for row in unit_brief:
        by_sku[row['sku_id']].append(row)

    sku_cards = []
    for sku in SKU.objects.filter(is_active=True).order_by('code'):
        sku_units = by_sku.get(sku.id, [])
        total = len(sku_units)
        warehouse = sum(1 for u in sku_units if u['status'] == 'in_warehouse')
        transit = sum(1 for u in sku_units if u['status'] == 'in_transit')
        maintenance = sum(1 for u in sku_units if u['status'] == 'maintenance')
        scrapped = sum(1 for u in sku_units if u['status'] == 'scrapped')
        transfer_transit = 0
        exception_count = 0
        max_out_days = 0
        health_scores = []
        for u in sku_units:
            mv = latest_by_unit.get(u['id'])
            if not mv:
                continue
            if mv.event_type in ['TRANSFER_PENDING', 'TRANSFER_SHIPPED']:
                transfer_transit += 1
            if mv.status in ['warning', 'timeout'] or mv.event_type == 'EXCEPTION':
                exception_count += 1
            if u['status'] != 'in_warehouse':
                days = (now - mv.event_time).days
                if days > max_out_days:
                    max_out_days = days
            score, _ = _compute_unit_health(
                unit_status=u['status'],
                hop_count=hop_counts.get(u['id'], 0),
                outbound_days=(now - mv.event_time).days if mv else 0,
                warn_reason=warn_reason_by_unit.get(u['id'], ''),
                latest_event_type=(mv.event_type if mv else ''),
            )
            health_scores.append(score)
        sku_cards.append({
            'sku': sku,
            'total': total,
            'warehouse': warehouse,
            'transit': transit,
            'transfer_transit': transfer_transit,
            'max_out_days': max_out_days,
            'exception_count': exception_count,
            'maintenance': maintenance,
            'scrapped': scrapped,
            'avg_health_score': round(sum(health_scores) / len(health_scores), 1) if health_scores else 100.0,
            'is_warn': (max_out_days > warn_outbound_days) or (exception_count > 0),
        })

    # 预警排序与异常池
    unit_list = list(units)
    enriched = []
    for unit in unit_list:
        latest_mv = latest_by_unit.get(unit.id)
        hop_count = hop_counts.get(unit.id, 0)
        outbound_days = (now - latest_mv.event_time).days if (latest_mv and unit.status != 'in_warehouse') else 0
        warn_reason = warn_reason_by_unit.get(unit.id, '')
        severity = 0
        if '转寄节点' in warn_reason:
            severity += 3
        if '在途>' in warn_reason:
            severity += 3
        if '待执行>' in warn_reason:
            severity += 2
        if '转寄在途>' in warn_reason:
            severity += 2
        health_score, health_level = _compute_unit_health(
            unit_status=unit.status,
            hop_count=hop_count,
            outbound_days=outbound_days,
            warn_reason=warn_reason,
            latest_event_type=(latest_mv.event_type if latest_mv else ''),
        )
        enriched.append((unit, latest_mv, hop_count, outbound_days, warn_reason, severity, health_score, health_level))

    if anomaly_only:
        enriched = [row for row in enriched if row[4]]

    # 默认预警排序：严重程度 desc -> 最新节点时间 desc -> 单套编号
    enriched.sort(
        key=lambda row: (
            -(row[5] or 0),
            -(row[1].event_time.timestamp() if row[1] else 0),
            row[0].unit_no
        )
    )

    units_page = Paginator(enriched, _get_page_size('page_size_outbound_units')).get_page(request.GET.get('page'))
    page_units = []
    for unit, latest_mv, hop_count, outbound_days, warn_reason, severity, health_score, health_level in units_page.object_list:
        unit.latest_movement = latest_mv
        unit.hop_count = hop_count
        unit.outbound_days = outbound_days
        unit.warn_reason = warn_reason
        unit.warn_severity = severity
        unit.health_score = health_score
        unit.health_level = health_level
        page_units.append(unit)
    units_page.object_list = page_units

    unit_ids_on_page = [u.id for u in units_page.object_list]
    parts_rows = list(
        InventoryUnitPart.objects.filter(unit_id__in=unit_ids_on_page, is_active=True).select_related('part').values(
            'unit_id', 'part_id', 'part__name', 'part__spec', 'status', 'expected_quantity', 'actual_quantity', 'notes', 'last_checked_at'
        )
    )
    part_summary_map = defaultdict(lambda: {'total': 0, 'missing': 0, 'damaged': 0, 'lost': 0, 'issue': 0})
    part_rows_map = defaultdict(list)
    for row in parts_rows:
        summary = part_summary_map[row['unit_id']]
        summary['total'] += 1
        status_val = row['status']
        if status_val in ['missing', 'damaged', 'lost']:
            summary[status_val] += 1
            summary['issue'] += 1
        part_rows_map[row['unit_id']].append({
            'part_id': row['part_id'],
            'part_name': row['part__name'],
            'part_spec': row['part__spec'] or '',
            'status': row['status'],
            'expected_quantity': int(row['expected_quantity'] or 0),
            'actual_quantity': int(row['actual_quantity'] or 0),
            'notes': row['notes'] or '',
            'last_checked_at': row['last_checked_at'].strftime('%Y-%m-%d %H:%M') if row['last_checked_at'] else '',
        })

    for unit in units_page.object_list:
        unit.latest_movement = latest_by_unit.get(unit.id)
        unit.hop_count = hop_counts.get(unit.id, getattr(unit, 'hop_count', 0))
        unit.outbound_days = getattr(unit, 'outbound_days', (now - unit.latest_movement.event_time).days if (unit.latest_movement and unit.status != 'in_warehouse') else 0)
        unit.warn_reason = getattr(unit, 'warn_reason', warn_reason_by_unit.get(unit.id, ''))
        unit.part_summary = part_summary_map.get(unit.id, {'total': 0, 'missing': 0, 'damaged': 0, 'lost': 0, 'issue': 0})
        unit.part_rows = part_rows_map.get(unit.id, [])
        if not hasattr(unit, 'health_score') or not hasattr(unit, 'health_level'):
            score, level = _compute_unit_health(
                unit_status=unit.status,
                hop_count=unit.hop_count,
                outbound_days=unit.outbound_days,
                warn_reason=unit.warn_reason,
                latest_event_type=(unit.latest_movement.event_type if unit.latest_movement else ''),
            )
            unit.health_score = score
            unit.health_level = level

    # 节点时间线
    timeline_qs = UnitMovement.objects.select_related(
        'unit__sku', 'from_order', 'to_order', 'transfer'
    ).order_by('-event_time')
    if event_type:
        timeline_qs = timeline_qs.filter(event_type=event_type)
    if sku_id.isdigit():
        timeline_qs = timeline_qs.filter(unit__sku_id=int(sku_id))
    timeline_page = Paginator(timeline_qs, _get_page_size('page_size_outbound_timeline')).get_page(request.GET.get('timeline_page'))

    # 拓扑文本视图（按单套串联节点）
    topology_rows = []
    topology_units_qs = InventoryUnit.objects.select_related('sku').filter(is_active=True).order_by('sku__code', 'unit_no')
    if topology_sku_id.isdigit():
        topology_units_qs = topology_units_qs.filter(sku_id=int(topology_sku_id))
    if topology_unit_no:
        topology_units_qs = topology_units_qs.filter(unit_no__icontains=topology_unit_no)
    if anomaly_only:
        topology_units_qs = topology_units_qs.filter(id__in=list(warn_reason_by_unit.keys()))
    topology_units_page = Paginator(topology_units_qs, _get_page_size('page_size_outbound_topology_units', 5)).get_page(request.GET.get('topology_page'))
    topology_units = list(topology_units_page.object_list)
    topology_graphs = []
    for unit in topology_units:
        unit_moves = UnitMovement.objects.filter(unit=unit).select_related('from_order', 'to_order', 'transfer').order_by('event_time')
        chain = []
        node_labels = []
        for mv in unit_moves:
            label = dict(UnitMovement.EVENT_CHOICES).get(mv.event_type, mv.event_type)
            node_labels.append(label)
            if mv.from_order and mv.to_order:
                chain.append(f"{label}({mv.from_order.order_no}->{mv.to_order.order_no})")
            elif mv.to_order:
                chain.append(f"{label}(->{mv.to_order.order_no})")
            elif mv.from_order:
                chain.append(f"{label}({mv.from_order.order_no}->)")
            else:
                chain.append(label)
        topology_rows.append({
            'unit_no': unit.unit_no,
            'sku': unit.sku,
            'hop_count': hop_counts.get(unit.id, 0),
            'chain_text': " -> ".join(chain) if chain else "-",
            'status': unit.get_status_display(),
            'warn_reason': warn_reason_by_unit.get(unit.id, ''),
        })
        nodes = []
        links = []
        if not node_labels:
            node_labels = ['无节点']
        x_step = 170
        width = max(220, 60 + x_step * max(len(node_labels) - 1, 1))
        move_list = list(unit_moves)
        for idx, nlabel in enumerate(node_labels):
            x = 30 + idx * x_step
            y = 36
            node_url = ''
            node_hint = nlabel
            fill = '#eef4ff'
            stroke = '#2f6fed'
            text = '#334155'
            if idx < len(move_list):
                mv = move_list[idx]
                if mv.to_order_id:
                    node_url = reverse('order_detail', kwargs={'order_id': mv.to_order_id})
                    node_hint = f"{nlabel} -> {mv.to_order.order_no}"
                elif mv.from_order_id:
                    node_url = reverse('order_detail', kwargs={'order_id': mv.from_order_id})
                    node_hint = f"{nlabel} <- {mv.from_order.order_no}"
                elif mv.transfer_id:
                    node_url = reverse('transfers_list') + f"?panel=tasks&status=all&keyword={mv.transfer_id}"
                    node_hint = f"{nlabel} / 任务#{mv.transfer_id}"
                age_hours = (now - mv.event_time).total_seconds() / 3600
                age_days = (now - mv.event_time).days
                if mv.status == 'timeout':
                    fill, stroke, text = '#fee2e2', '#dc2626', '#991b1b'
                elif (
                    mv.status == 'warning'
                    or mv.event_type == 'EXCEPTION'
                    or (mv.event_type == 'TRANSFER_PENDING' and age_hours > pending_timeout_hours)
                    or (mv.event_type == 'TRANSFER_SHIPPED' and age_days > shipped_timeout_days)
                ):
                    fill, stroke, text = '#fff4e5', '#f59e0b', '#92400e'
            nodes.append({
                'label': nlabel,
                'x': x,
                'y': y,
                'url': node_url,
                'hint': node_hint,
                'fill': fill,
                'stroke': stroke,
                'text': text,
            })
            if idx > 0:
                prev_x = 30 + (idx - 1) * x_step
                links.append({'x1': prev_x + 14, 'y1': y, 'x2': x - 14, 'y2': y})
        topology_graphs.append({
            'unit_no': unit.unit_no,
            'sku': unit.sku,
            'width': width,
            'nodes': nodes,
            'links': links,
        })

    context = {
        'summary': summary,
        'sku_cards': sku_cards,
        'units': units_page,
        'units_page': units_page,
        'timeline': timeline_page,
        'timeline_page': timeline_page,
        'topology_rows': topology_rows,
        'topology_graphs': topology_graphs,
        'topology_units_page': topology_units_page,
        'topology_sku_filter': topology_sku_id,
        'topology_unit_no_filter': topology_unit_no,
        'status_filter': status,
        'sku_filter': sku_id,
        'keyword': keyword,
        'event_type_filter': event_type,
        'anomaly_only': anomaly_only,
        'part_issue_only': part_issue_only,
        'status_choices': InventoryUnit.STATUS_CHOICES,
        'event_type_choices': UnitMovement.EVENT_CHOICES,
        'skus': SKU.objects.filter(is_active=True).order_by('code'),
        'warn_outbound_days': warn_outbound_days,
        'warn_hops': warn_hops,
        'pending_timeout_hours': pending_timeout_hours,
        'shipped_timeout_days': shipped_timeout_days,
        'pagination_query': _build_querystring(request, ['page']),
        'timeline_pagination_query': _build_querystring(request, ['timeline_page']),
        'topology_pagination_query': _build_querystring(request, ['topology_page']),
        'unit_parts_map_json': json.dumps({
            str(unit.id): getattr(unit, 'part_rows', []) for unit in units_page.object_list
        }, ensure_ascii=False),
        'parts_inventory': Part.objects.filter(is_active=True).order_by('name'),
        'maintenance_work_orders': MaintenanceWorkOrder.objects.select_related('unit', 'sku').order_by('-created_at')[:20],
        'disposal_orders': UnitDisposalOrder.objects.select_related('unit', 'sku').order_by('-created_at')[:20],
    }
    return render(request, 'outbound_inventory.html', context)


@login_required
@require_permission('outbound_inventory', 'update')
def outbound_inventory_unit_parts_update(request, unit_id):
    """更新单套部件状态盘点结果"""
    if request.method != 'POST':
        return redirect('outbound_inventory_dashboard')

    unit = get_object_or_404(InventoryUnit, id=unit_id, is_active=True)
    part_ids = request.POST.getlist('part_id[]')
    statuses = request.POST.getlist('status[]')
    actual_qtys = request.POST.getlist('actual_quantity[]')
    notes_list = request.POST.getlist('notes[]')

    try:
        updated = 0
        before_rows = []
        after_rows = []
        with transaction.atomic():
            for idx, part_id_raw in enumerate(part_ids):
                part_id_raw = (part_id_raw or '').strip()
                if not part_id_raw.isdigit():
                    continue
                status_val = (statuses[idx] if idx < len(statuses) else 'normal').strip() or 'normal'
                if status_val not in ['normal', 'missing', 'damaged', 'lost']:
                    status_val = 'normal'
                qty_raw = (actual_qtys[idx] if idx < len(actual_qtys) else '').strip()
                notes = (notes_list[idx] if idx < len(notes_list) else '').strip()
                actual_qty = int(qty_raw or '0')
                if actual_qty < 0:
                    actual_qty = 0

                row = InventoryUnitPart.objects.filter(
                    unit=unit,
                    part_id=int(part_id_raw),
                    is_active=True
                ).first()
                if not row:
                    continue
                before_rows.append({
                    'part_id': row.part_id,
                    'status': row.status,
                    'actual_quantity': int(row.actual_quantity or 0),
                    'notes': row.notes or '',
                })
                row.status = status_val
                row.actual_quantity = actual_qty
                row.notes = notes[:200]
                row.last_checked_at = timezone.now()
                row.save(update_fields=['status', 'actual_quantity', 'notes', 'last_checked_at', 'updated_at'])
                after_rows.append({
                    'part_id': row.part_id,
                    'status': row.status,
                    'actual_quantity': int(row.actual_quantity or 0),
                    'notes': row.notes or '',
                })
                updated += 1

        if updated > 0:
            AuditService.log_with_diff(
                user=request.user,
                action='update',
                module='在外库存',
                target=unit.unit_no,
                summary='更新单套部件盘点',
                before={'items': before_rows},
                after={'items': after_rows},
                extra={'unit_id': unit.id, 'updated_count': updated},
            )

        messages.success(request, f'单套 {unit.unit_no} 部件盘点已保存（{updated} 项）')
    except Exception as e:
        messages.error(request, f'保存失败：{str(e)}')

    return redirect('outbound_inventory_dashboard')


@login_required
@require_permission('outbound_inventory', 'update')
def maintenance_work_order_create(request, unit_id):
    """创建维修换件工单"""
    if request.method != 'POST':
        return redirect('outbound_inventory_dashboard')
    unit = get_object_or_404(InventoryUnit, id=unit_id, is_active=True)
    try:
        items = _parse_maintenance_items_post(request)
        issue_desc = (request.POST.get('issue_desc') or '').strip()
        notes = (request.POST.get('notes') or '').strip()
        work_order = MaintenanceService.create_work_order(
            unit=unit,
            issue_desc=issue_desc,
            items=items,
            notes=notes,
            user=request.user,
        )
        messages.success(request, f'维修工单已创建：{work_order.work_order_no}')
    except Exception as e:
        messages.error(request, f'创建维修工单失败：{str(e)}')
    return redirect('outbound_inventory_dashboard')


@login_required
@require_permission('outbound_inventory', 'update')
def maintenance_work_order_complete(request, work_order_id):
    """执行维修换件工单"""
    if request.method != 'POST':
        return redirect('outbound_inventory_dashboard')
    work_order = get_object_or_404(MaintenanceWorkOrder.objects.select_related('unit', 'sku'), id=work_order_id)
    try:
        MaintenanceService.complete_work_order(work_order=work_order, user=request.user)
        messages.success(request, f'维修工单 {work_order.work_order_no} 已完成')
    except Exception as e:
        messages.error(request, f'执行维修工单失败：{str(e)}')
    return redirect('outbound_inventory_dashboard')


@login_required
@require_permission('outbound_inventory', 'update')
def maintenance_work_order_cancel(request, work_order_id):
    """取消维修换件工单"""
    if request.method != 'POST':
        return redirect('maintenance_work_orders_list')
    work_order = get_object_or_404(MaintenanceWorkOrder.objects.select_related('unit', 'sku'), id=work_order_id)
    try:
        MaintenanceService.cancel_work_order(work_order=work_order, user=request.user)
        messages.success(request, f'维修工单 {work_order.work_order_no} 已取消')
    except Exception as e:
        messages.error(request, f'取消维修工单失败：{str(e)}')
    next_url = request.POST.get('next') or reverse('maintenance_work_orders_list')
    return redirect(next_url)


@login_required
@require_permission('outbound_inventory', 'update')
def maintenance_work_order_reverse(request, work_order_id):
    """冲销已完成维修工单"""
    if request.method != 'POST':
        return redirect('maintenance_work_orders_list')
    work_order = get_object_or_404(MaintenanceWorkOrder.objects.select_related('unit', 'sku'), id=work_order_id)
    try:
        MaintenanceService.reverse_work_order(work_order=work_order, user=request.user)
        messages.success(request, f'维修工单 {work_order.work_order_no} 已冲销')
    except Exception as e:
        messages.error(request, f'维修工单冲销失败：{str(e)}')
    next_url = request.POST.get('next') or reverse('maintenance_work_orders_list')
    return redirect(next_url)


@login_required
@require_permission('outbound_inventory', 'view')
def maintenance_work_orders_list(request):
    """维修工单列表"""
    orders = MaintenanceWorkOrder.objects.select_related(
        'unit', 'sku', 'created_by', 'completed_by'
    ).prefetch_related('items__old_part', 'items__new_part').order_by('-created_at')
    keyword = (request.GET.get('keyword', '') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    sku_filter = (request.GET.get('sku_id') or '').strip()
    part_filter = (request.GET.get('part_id') or '').strip()
    from_report = (request.GET.get('from_report') or '').strip() == '1'
    range_filter = (request.GET.get('range') or '').strip()
    if keyword:
        orders = orders.filter(
            Q(work_order_no__icontains=keyword) |
            Q(unit__unit_no__icontains=keyword) |
            Q(sku__code__icontains=keyword) |
            Q(sku__name__icontains=keyword) |
            Q(unit__current_order__order_no__icontains=keyword) |
            Q(issue_desc__icontains=keyword) |
            Q(created_by__username__icontains=keyword) |
            Q(items__old_part__name__icontains=keyword) |
            Q(items__new_part__name__icontains=keyword)
        ).distinct()
    if status_filter:
        orders = orders.filter(status=status_filter)
    if sku_filter:
        orders = orders.filter(sku_id=sku_filter)
    if part_filter:
        orders = orders.filter(
            Q(items__old_part_id=part_filter) | Q(items__new_part_id=part_filter)
        ).distinct()
    page = Paginator(orders, _get_page_size('page_size_maintenance_orders')).get_page(request.GET.get('page'))
    for order in page.object_list:
        order.items_summary = '，'.join([
            f"{item.old_part.name}->{item.new_part.name} x{item.replace_quantity}" for item in order.items.all()[:4]
        ]) or '-'
    context = {
        'maintenance_orders': page,
        'maintenance_orders_page': page,
        'keyword': keyword,
        'status_filter': status_filter,
        'sku_filter': sku_filter,
        'part_filter': part_filter,
        'from_report': from_report,
        'report_back_query': urlencode({
            key: value for key, value in {
                'range': range_filter,
                'sku_id': sku_filter,
                'part_id': part_filter,
            }.items() if value not in ['', None]
        }),
        'status_choices': MaintenanceWorkOrder.STATUS_CHOICES,
        'sku_choices': SKU.objects.filter(is_active=True).order_by('code'),
        'part_choices': Part.objects.filter(is_active=True).order_by('name'),
        'pagination_query': _build_querystring(request, ['page']),
        'summary': {
            'total_count': orders.count(),
            'draft_count': orders.filter(status='draft').count(),
            'completed_count': orders.filter(status='completed').count(),
            'reversed_count': orders.filter(status='reversed').count(),
        },
    }
    return render(request, 'procurement/maintenance_work_orders.html', context)


@login_required
@require_permission('outbound_inventory', 'view')
def maintenance_work_orders_export(request):
    """导出维修工单CSV"""
    orders = MaintenanceWorkOrder.objects.select_related(
        'unit', 'sku', 'created_by', 'completed_by'
    ).prefetch_related('items__old_part', 'items__new_part').order_by('-created_at')
    keyword = (request.GET.get('keyword', '') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    sku_filter = (request.GET.get('sku_id') or '').strip()
    part_filter = (request.GET.get('part_id') or '').strip()
    if keyword:
        orders = orders.filter(
            Q(work_order_no__icontains=keyword) |
            Q(unit__unit_no__icontains=keyword) |
            Q(sku__code__icontains=keyword) |
            Q(sku__name__icontains=keyword) |
            Q(unit__current_order__order_no__icontains=keyword) |
            Q(issue_desc__icontains=keyword) |
            Q(created_by__username__icontains=keyword) |
            Q(items__old_part__name__icontains=keyword) |
            Q(items__new_part__name__icontains=keyword)
        ).distinct()
    if status_filter:
        orders = orders.filter(status=status_filter)
    if sku_filter:
        orders = orders.filter(sku_id=sku_filter)
    if part_filter:
        orders = orders.filter(
            Q(items__old_part_id=part_filter) | Q(items__new_part_id=part_filter)
        ).distinct()

    resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    resp['Content-Disposition'] = 'attachment; filename="maintenance_work_orders.csv"'
    writer = csv.writer(resp)
    writer.writerow([
        '工单号', '单套编号', 'SKU编码', 'SKU名称', '换件明细', '问题描述',
        '状态', '创建人', '创建时间', '完成人', '完成时间'
    ])
    for order in orders:
        items_summary = '，'.join([
            f"{item.old_part.name}->{item.new_part.name} x{item.replace_quantity}" for item in order.items.all()
        ]) or '-'
        writer.writerow([
            order.work_order_no,
            order.unit.unit_no if order.unit else '',
            order.sku.code if order.sku else '',
            order.sku.name if order.sku else '',
            items_summary,
            order.issue_desc or '',
            order.get_status_display(),
            order.created_by.username if order.created_by else '',
            order.created_at.strftime('%Y-%m-%d %H:%M:%S') if order.created_at else '',
            order.completed_by.username if order.completed_by else '',
            order.completed_at.strftime('%Y-%m-%d %H:%M:%S') if order.completed_at else '',
        ])
    return resp


@login_required
@require_permission('outbound_inventory', 'view')
def part_issue_pool(request):
    """部件折损池 / 待维修池"""
    keyword = (request.GET.get('keyword', '') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    sku_filter = (request.GET.get('sku_id') or '').strip()
    part_filter = (request.GET.get('part_id') or '').strip()
    from_report = (request.GET.get('from_report') or '').strip() == '1'
    range_filter = (request.GET.get('range') or '').strip()
    anomaly_qs = InventoryUnitPart.objects.select_related(
        'unit', 'unit__sku', 'unit__current_order', 'part'
    ).filter(
        is_active=True,
        status__in=['missing', 'damaged', 'lost']
    ).order_by('-updated_at', 'unit__unit_no', 'part__name')
    if keyword:
        anomaly_qs = anomaly_qs.filter(
            Q(unit__unit_no__icontains=keyword) |
            Q(unit__sku__code__icontains=keyword) |
            Q(unit__sku__name__icontains=keyword) |
            Q(part__name__icontains=keyword) |
            Q(unit__current_order__order_no__icontains=keyword) |
            Q(notes__icontains=keyword)
        )
    if status_filter in ['missing', 'damaged', 'lost']:
        anomaly_qs = anomaly_qs.filter(status=status_filter)
    if sku_filter:
        anomaly_qs = anomaly_qs.filter(unit__sku_id=sku_filter)
    if part_filter:
        anomaly_qs = anomaly_qs.filter(part_id=part_filter)

    maintenance_qs = MaintenanceWorkOrder.objects.select_related(
        'unit', 'sku', 'created_by'
    ).prefetch_related('items__old_part', 'items__new_part').filter(status='draft').order_by('-created_at')
    if keyword:
        maintenance_qs = maintenance_qs.filter(
            Q(work_order_no__icontains=keyword) |
            Q(unit__unit_no__icontains=keyword) |
            Q(sku__code__icontains=keyword) |
            Q(sku__name__icontains=keyword) |
            Q(unit__current_order__order_no__icontains=keyword) |
            Q(issue_desc__icontains=keyword) |
            Q(items__old_part__name__icontains=keyword) |
            Q(items__new_part__name__icontains=keyword)
        ).distinct()
    if sku_filter:
        maintenance_qs = maintenance_qs.filter(sku_id=sku_filter)
    if part_filter:
        maintenance_qs = maintenance_qs.filter(
            Q(items__old_part_id=part_filter) | Q(items__new_part_id=part_filter)
        ).distinct()

    anomaly_page = Paginator(anomaly_qs, _get_page_size('page_size_part_issue_pool')).get_page(request.GET.get('anomaly_page'))
    maintenance_page = Paginator(maintenance_qs, _get_page_size('page_size_part_issue_maintenance')).get_page(request.GET.get('maintenance_page'))

    summary = {
        'issue_units': anomaly_qs.values('unit_id').distinct().count(),
        'issue_rows': anomaly_qs.count(),
        'draft_maintenance': maintenance_qs.count(),
        'damaged_count': anomaly_qs.filter(status='damaged').count(),
    }
    for order in maintenance_page.object_list:
        order.items_summary = '，'.join([
            f"{item.old_part.name}->{item.new_part.name} x{item.replace_quantity}" for item in order.items.all()[:4]
        ]) or '-'

    context = {
        'keyword': keyword,
        'status_filter': status_filter,
        'sku_filter': sku_filter,
        'part_filter': part_filter,
        'from_report': from_report,
        'report_back_query': urlencode({
            key: value for key, value in {
                'range': range_filter,
                'sku_id': sku_filter,
                'part_id': part_filter,
            }.items() if value not in ['', None]
        }),
        'status_choices': InventoryUnitPart.STATUS_CHOICES,
        'sku_choices': SKU.objects.filter(is_active=True).order_by('code'),
        'part_choices': Part.objects.filter(is_active=True).order_by('name'),
        'summary': summary,
        'anomaly_rows': anomaly_page,
        'anomaly_page': anomaly_page,
        'maintenance_orders': maintenance_page,
        'maintenance_page': maintenance_page,
        'anomaly_pagination_query': _build_querystring(request, ['anomaly_page']),
        'maintenance_pagination_query': _build_querystring(request, ['maintenance_page']),
    }
    return render(request, 'procurement/part_issue_pool.html', context)


@login_required
@require_permission('parts', 'view')
def part_recovery_inspections_list(request):
    """拆解回件质检池"""
    inspections = PartRecoveryInspection.objects.select_related(
        'disposal_order', 'disposal_item', 'unit', 'sku', 'part', 'processed_by'
    ).order_by('-created_at')
    keyword = (request.GET.get('keyword', '') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    sku_filter = (request.GET.get('sku_id') or '').strip()
    part_filter = (request.GET.get('part_id') or '').strip()
    from_report = (request.GET.get('from_report') or '').strip() == '1'
    range_filter = (request.GET.get('range') or '').strip()
    if keyword:
        inspections = inspections.filter(
            Q(disposal_order__disposal_no__icontains=keyword)
            | Q(unit__unit_no__icontains=keyword)
            | Q(sku__code__icontains=keyword)
            | Q(sku__name__icontains=keyword)
            | Q(part__name__icontains=keyword)
            | Q(unit__current_order__order_no__icontains=keyword)
            | Q(notes__icontains=keyword)
        ).distinct()
    if status_filter:
        inspections = inspections.filter(status=status_filter)
    if sku_filter:
        inspections = inspections.filter(sku_id=sku_filter)
    if part_filter:
        inspections = inspections.filter(part_id=part_filter)

    page = Paginator(
        inspections,
        _get_page_size('page_size_part_recovery_inspections')
    ).get_page(request.GET.get('page'))
    context = {
        'inspections': page,
        'inspections_page': page,
        'keyword': keyword,
        'status_filter': status_filter,
        'sku_filter': sku_filter,
        'part_filter': part_filter,
        'from_report': from_report,
        'report_back_query': urlencode({
            key: value for key, value in {
                'range': range_filter,
                'sku_id': sku_filter,
                'part_id': part_filter,
            }.items() if value not in ['', None]
        }),
        'status_choices': PartRecoveryInspection.STATUS_CHOICES,
        'sku_choices': SKU.objects.filter(is_active=True).order_by('code'),
        'part_choices': Part.objects.filter(is_active=True).order_by('name'),
        'pagination_query': _build_querystring(request, ['page']),
        'summary': {
            'pending_count': inspections.filter(status='pending').count(),
            'returned_count': inspections.filter(status='returned').count(),
            'repair_count': inspections.filter(status='repair').count(),
            'scrapped_count': inspections.filter(status='scrapped').count(),
        },
    }
    return render(request, 'procurement/part_recovery_inspections.html', context)


@login_required
@require_permission('parts', 'view')
def part_recovery_inspections_export(request):
    """导出拆解回件质检池CSV"""
    inspections = PartRecoveryInspection.objects.select_related(
        'disposal_order', 'unit', 'sku', 'part', 'processed_by'
    ).order_by('-created_at')
    keyword = (request.GET.get('keyword', '') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    sku_filter = (request.GET.get('sku_id') or '').strip()
    part_filter = (request.GET.get('part_id') or '').strip()
    if keyword:
        inspections = inspections.filter(
            Q(disposal_order__disposal_no__icontains=keyword)
            | Q(unit__unit_no__icontains=keyword)
            | Q(sku__code__icontains=keyword)
            | Q(sku__name__icontains=keyword)
            | Q(part__name__icontains=keyword)
            | Q(unit__current_order__order_no__icontains=keyword)
            | Q(notes__icontains=keyword)
        ).distinct()
    if status_filter:
        inspections = inspections.filter(status=status_filter)
    if sku_filter:
        inspections = inspections.filter(sku_id=sku_filter)
    if part_filter:
        inspections = inspections.filter(part_id=part_filter)

    resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    resp['Content-Disposition'] = 'attachment; filename="part_recovery_inspections.csv"'
    writer = csv.writer(resp)
    writer.writerow([
        '来源处置单', '单套编号', 'SKU编码', 'SKU名称', '部件', '数量',
        '状态', '处理人', '处理时间', '备注', '创建时间'
    ])
    for inspection in inspections:
        writer.writerow([
            inspection.disposal_order.disposal_no if inspection.disposal_order else '',
            inspection.unit.unit_no if inspection.unit else '',
            inspection.sku.code if inspection.sku else '',
            inspection.sku.name if inspection.sku else '',
            inspection.part.name if inspection.part else '',
            inspection.quantity,
            inspection.get_status_display(),
            inspection.processed_by.username if inspection.processed_by else '',
            inspection.processed_at.strftime('%Y-%m-%d %H:%M:%S') if inspection.processed_at else '',
            inspection.notes or '',
            inspection.created_at.strftime('%Y-%m-%d %H:%M:%S') if inspection.created_at else '',
        ])
    return resp


@login_required
@require_permission('parts', 'view')
def warehouse_reports(request):
    """仓储报表总览"""
    range_days = (request.GET.get('range') or '7').strip()
    try:
        range_days = int(range_days)
    except (TypeError, ValueError):
        range_days = 7
    range_days = 30 if range_days == 30 else 7

    sku_filter = (request.GET.get('sku_id') or '').strip()
    part_filter = (request.GET.get('part_id') or '').strip()

    assembly_qs = AssemblyOrder.objects.select_related('sku').prefetch_related('items__part')
    maintenance_qs = MaintenanceWorkOrder.objects.select_related('sku', 'unit').prefetch_related('items__old_part', 'items__new_part')
    disposal_qs = UnitDisposalOrder.objects.select_related('sku', 'unit').prefetch_related('items__part')
    recovery_qs = PartRecoveryInspection.objects.select_related('part', 'sku', 'unit')

    if sku_filter.isdigit():
        sku_id = int(sku_filter)
        assembly_qs = assembly_qs.filter(sku_id=sku_id)
        maintenance_qs = maintenance_qs.filter(sku_id=sku_id)
        disposal_qs = disposal_qs.filter(sku_id=sku_id)
        recovery_qs = recovery_qs.filter(sku_id=sku_id)
    if part_filter.isdigit():
        part_id = int(part_filter)
        assembly_qs = assembly_qs.filter(items__part_id=part_id).distinct()
        maintenance_qs = maintenance_qs.filter(
            Q(items__old_part_id=part_id) | Q(items__new_part_id=part_id)
        ).distinct()
        disposal_qs = disposal_qs.filter(items__part_id=part_id).distinct()
        recovery_qs = recovery_qs.filter(part_id=part_id)

    summary = {
        'assembly_completed': assembly_qs.filter(status='completed').count(),
        'maintenance_draft': maintenance_qs.filter(status='draft').count(),
        'maintenance_completed': maintenance_qs.filter(status='completed').count(),
        'disposal_completed': disposal_qs.filter(status='completed').count(),
        'recovery_pending': recovery_qs.filter(status='pending').count(),
        'recovery_repair': recovery_qs.filter(status='repair').count(),
    }

    trends = [
        {
            'label': '装配完成',
            'color': '#2563eb',
            'rows': _count_by_day(assembly_qs.filter(status='completed', completed_at__isnull=False), 'completed_at', range_days),
        },
        {
            'label': '新建维修单',
            'color': '#14b8a6',
            'rows': _count_by_day(maintenance_qs, 'created_at', range_days),
        },
        {
            'label': '单套处置完成',
            'color': '#f97316',
            'rows': _count_by_day(disposal_qs.filter(status='completed', completed_at__isnull=False), 'completed_at', range_days),
        },
        {
            'label': '回件待质检新增',
            'color': '#8b5cf6',
            'rows': _count_by_day(recovery_qs.filter(status='pending'), 'created_at', range_days),
        },
        {
            'label': '回件回库完成',
            'color': '#16a34a',
            'rows': _count_by_day(recovery_qs.filter(status='returned', processed_at__isnull=False), 'processed_at', range_days),
        },
    ]
    for trend in trends:
        trend['max_value'] = max([row['value'] for row in trend['rows']] or [0])
        for row in trend['rows']:
            row['percent'] = round((row['value'] / trend['max_value']) * 100, 1) if trend['max_value'] else 0

    context = {
        'range_days': range_days,
        'sku_filter': sku_filter,
        'part_filter': part_filter,
        'sku_choices': SKU.objects.filter(is_active=True).order_by('code'),
        'part_choices': Part.objects.filter(is_active=True).order_by('name'),
        'export_query': urlencode({
            key: value for key, value in {
                'range': range_days,
                'sku_id': sku_filter,
                'part_id': part_filter,
            }.items() if value not in ['', None]
        }),
        'summary': summary,
        'trends': trends,
        'assembly_distribution': _build_distribution([
            {'label': '已完成', 'value': assembly_qs.filter(status='completed').count(), 'color': '#2563eb'},
            {'label': '已取消', 'value': assembly_qs.filter(status='cancelled').count(), 'color': '#94a3b8'},
        ]),
        'maintenance_distribution': _build_distribution([
            {'label': '待执行', 'value': maintenance_qs.filter(status='draft').count(), 'color': '#f59e0b'},
            {'label': '已完成', 'value': maintenance_qs.filter(status='completed').count(), 'color': '#10b981'},
            {'label': '已冲销', 'value': maintenance_qs.filter(status='reversed').count(), 'color': '#64748b'},
            {'label': '已取消', 'value': maintenance_qs.filter(status='cancelled').count(), 'color': '#ef4444'},
        ]),
        'disposal_distribution': _build_distribution([
            {'label': '拆解回件', 'value': disposal_qs.filter(action_type='disassemble').count(), 'color': '#06b6d4'},
            {'label': '报废停用', 'value': disposal_qs.filter(action_type='scrap').count(), 'color': '#ef4444'},
        ]),
        'recovery_distribution': _build_distribution([
            {'label': '待质检', 'value': recovery_qs.filter(status='pending').count(), 'color': '#f59e0b'},
            {'label': '待维修', 'value': recovery_qs.filter(status='repair').count(), 'color': '#3b82f6'},
            {'label': '已回库', 'value': recovery_qs.filter(status='returned').count(), 'color': '#16a34a'},
            {'label': '已报废', 'value': recovery_qs.filter(status='scrapped').count(), 'color': '#6b7280'},
        ]),
        'issue_top_parts': Part.objects.filter(is_active=True).annotate(
            issue_rows=Count(
                'inventory_unit_parts',
                filter=Q(
                    inventory_unit_parts__is_active=True,
                    inventory_unit_parts__status__in=['missing', 'damaged', 'lost']
                ) & (Q(inventory_unit_parts__unit__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
            )
        ).filter(
            issue_rows__gt=0,
            **({'id': int(part_filter)} if part_filter.isdigit() else {})
        ).order_by('-issue_rows', 'name')[:8],
        'maintenance_top_parts': Part.objects.filter(is_active=True).annotate(
            replace_total=Sum(
                'maintenance_new_items__replace_quantity',
                filter=(Q(maintenance_new_items__work_order__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
            )
        ).filter(
            replace_total__gt=0,
            **({'id': int(part_filter)} if part_filter.isdigit() else {})
        ).order_by('-replace_total', 'name')[:8],
        'recovery_top_parts': Part.objects.filter(is_active=True).annotate(
            pending_qty=Sum(
                'recovery_inspections__quantity',
                filter=Q(recovery_inspections__status='pending') & (Q(recovery_inspections__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
            ),
            repair_qty=Sum(
                'recovery_inspections__quantity',
                filter=Q(recovery_inspections__status='repair') & (Q(recovery_inspections__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
            ),
            returned_qty=Sum(
                'recovery_inspections__quantity',
                filter=Q(recovery_inspections__status='returned') & (Q(recovery_inspections__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
            ),
            scrapped_qty=Sum(
                'recovery_inspections__quantity',
                filter=Q(recovery_inspections__status='scrapped') & (Q(recovery_inspections__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
            ),
        ).filter(
            Q(pending_qty__gt=0) | Q(repair_qty__gt=0) | Q(returned_qty__gt=0) | Q(scrapped_qty__gt=0),
            **({'id': int(part_filter)} if part_filter.isdigit() else {})
        ).order_by(
            '-pending_qty', '-repair_qty', '-returned_qty', '-scrapped_qty', 'name'
        )[:8],
    }
    return render(request, 'procurement/warehouse_reports.html', context)


@login_required
@require_permission('parts', 'view')
def warehouse_reports_export(request):
    """导出仓储报表CSV"""
    range_days = (request.GET.get('range') or '7').strip()
    try:
        range_days = int(range_days)
    except (TypeError, ValueError):
        range_days = 7
    range_days = 30 if range_days == 30 else 7

    sku_filter = (request.GET.get('sku_id') or '').strip()
    part_filter = (request.GET.get('part_id') or '').strip()

    assembly_qs = AssemblyOrder.objects.select_related('sku').prefetch_related('items__part')
    maintenance_qs = MaintenanceWorkOrder.objects.select_related('sku', 'unit').prefetch_related('items__old_part', 'items__new_part')
    disposal_qs = UnitDisposalOrder.objects.select_related('sku', 'unit').prefetch_related('items__part')
    recovery_qs = PartRecoveryInspection.objects.select_related('part', 'sku', 'unit')

    if sku_filter.isdigit():
        sku_id = int(sku_filter)
        assembly_qs = assembly_qs.filter(sku_id=sku_id)
        maintenance_qs = maintenance_qs.filter(sku_id=sku_id)
        disposal_qs = disposal_qs.filter(sku_id=sku_id)
        recovery_qs = recovery_qs.filter(sku_id=sku_id)
    if part_filter.isdigit():
        part_id = int(part_filter)
        assembly_qs = assembly_qs.filter(items__part_id=part_id).distinct()
        maintenance_qs = maintenance_qs.filter(
            Q(items__old_part_id=part_id) | Q(items__new_part_id=part_id)
        ).distinct()
        disposal_qs = disposal_qs.filter(items__part_id=part_id).distinct()
        recovery_qs = recovery_qs.filter(part_id=part_id)

    summary_rows = [
        ('累计完成装配', assembly_qs.filter(status='completed').count()),
        ('待执行维修单', maintenance_qs.filter(status='draft').count()),
        ('已完成维修单', maintenance_qs.filter(status='completed').count()),
        ('已完成单套处置', disposal_qs.filter(status='completed').count()),
        ('待质检回件', recovery_qs.filter(status='pending').count()),
        ('待维修回件', recovery_qs.filter(status='repair').count()),
    ]
    trend_sets = [
        ('装配完成', _count_by_day(assembly_qs.filter(status='completed', completed_at__isnull=False), 'completed_at', range_days)),
        ('新建维修单', _count_by_day(maintenance_qs, 'created_at', range_days)),
        ('单套处置完成', _count_by_day(disposal_qs.filter(status='completed', completed_at__isnull=False), 'completed_at', range_days)),
        ('回件待质检新增', _count_by_day(recovery_qs.filter(status='pending'), 'created_at', range_days)),
        ('回件回库完成', _count_by_day(recovery_qs.filter(status='returned', processed_at__isnull=False), 'processed_at', range_days)),
    ]
    issue_top_parts = Part.objects.filter(is_active=True).annotate(
        issue_rows=Count(
            'inventory_unit_parts',
            filter=Q(
                inventory_unit_parts__is_active=True,
                inventory_unit_parts__status__in=['missing', 'damaged', 'lost']
            ) & (Q(inventory_unit_parts__unit__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
        )
    ).filter(issue_rows__gt=0, **({'id': int(part_filter)} if part_filter.isdigit() else {})).order_by('-issue_rows', 'name')[:8]
    maintenance_top_parts = Part.objects.filter(is_active=True).annotate(
        replace_total=Sum(
            'maintenance_new_items__replace_quantity',
            filter=(Q(maintenance_new_items__work_order__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
        )
    ).filter(replace_total__gt=0, **({'id': int(part_filter)} if part_filter.isdigit() else {})).order_by('-replace_total', 'name')[:8]
    recovery_top_parts = Part.objects.filter(is_active=True).annotate(
        pending_qty=Sum(
            'recovery_inspections__quantity',
            filter=Q(recovery_inspections__status='pending') & (Q(recovery_inspections__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
        ),
        repair_qty=Sum(
            'recovery_inspections__quantity',
            filter=Q(recovery_inspections__status='repair') & (Q(recovery_inspections__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
        ),
        returned_qty=Sum(
            'recovery_inspections__quantity',
            filter=Q(recovery_inspections__status='returned') & (Q(recovery_inspections__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
        ),
        scrapped_qty=Sum(
            'recovery_inspections__quantity',
            filter=Q(recovery_inspections__status='scrapped') & (Q(recovery_inspections__sku_id=int(sku_filter)) if sku_filter.isdigit() else Q())
        ),
    ).filter(
        Q(pending_qty__gt=0) | Q(repair_qty__gt=0) | Q(returned_qty__gt=0) | Q(scrapped_qty__gt=0),
        **({'id': int(part_filter)} if part_filter.isdigit() else {})
    ).order_by('-pending_qty', '-repair_qty', '-returned_qty', '-scrapped_qty', 'name')[:8]

    resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    resp['Content-Disposition'] = f'attachment; filename=\"warehouse_reports_{range_days}d.csv\"'
    writer = csv.writer(resp)
    writer.writerow(['筛选条件', 'SKU', (SKU.objects.filter(id=int(sku_filter)).values_list('code', flat=True).first() if sku_filter.isdigit() else '全部')])
    writer.writerow(['筛选条件', '部件', (Part.objects.filter(id=int(part_filter)).values_list('name', flat=True).first() if part_filter.isdigit() else '全部')])
    writer.writerow(['模块', '指标', '日期', '值'])
    for label, value in summary_rows:
        writer.writerow(['汇总', label, '', value])
    for trend_label, rows in trend_sets:
        for row in rows:
            writer.writerow(['趋势', trend_label, row['date'], row['value']])
    writer.writerow([])
    writer.writerow(['榜单', '部件', '值1', '值2', '值3'])
    for part in issue_top_parts:
        writer.writerow(['高频损耗部件', part.name, part.issue_rows, '', ''])
    for part in maintenance_top_parts:
        writer.writerow(['维修替换排行', part.name, part.replace_total or 0, '', ''])
    for part in recovery_top_parts:
        writer.writerow([
            '回件质检结果排行',
            part.name,
            f"待质检:{part.pending_qty or 0}",
            f"待维修:{part.repair_qty or 0}",
            f"已回库:{part.returned_qty or 0}/已报废:{part.scrapped_qty or 0}",
        ])
    return resp


@login_required
@require_permission('parts', 'update')
def part_recovery_inspection_process(request, inspection_id):
    """处理拆解回件质检结果"""
    if request.method != 'POST':
        return redirect('part_recovery_inspections_list')
    inspection = get_object_or_404(PartRecoveryInspection, id=inspection_id)
    action_type = (request.POST.get('action_type') or '').strip()
    notes = (request.POST.get('notes') or '').strip()
    try:
        UnitDisposalService.process_recovery_inspection(
            inspection=inspection,
            action_type=action_type,
            notes=notes,
            user=request.user,
        )
        messages.success(request, '回件质检结果已处理')
    except Exception as e:
        messages.error(request, f'回件质检处理失败：{str(e)}')
    next_url = request.POST.get('next') or reverse('part_recovery_inspections_list')
    return redirect(next_url)


@login_required
@require_permission('outbound_inventory', 'update')
def unit_disposal_create(request, unit_id):
    """执行单套拆解/报废"""
    if request.method != 'POST':
        return redirect('outbound_inventory_dashboard')
    unit = get_object_or_404(InventoryUnit, id=unit_id)
    action_type = (request.POST.get('action_type') or '').strip()
    issue_desc = (request.POST.get('issue_desc') or '').strip()
    notes = (request.POST.get('notes') or '').strip()
    try:
        if has_action_permission(request.user, 'unit.dispose'):
            order = UnitDisposalService.create_and_complete(
                unit=unit,
                action_type=action_type,
                issue_desc=issue_desc,
                notes=notes,
                user=request.user,
            )
            messages.success(request, f'单套处置已完成：{order.disposal_no}')
        elif can_request_approval(request.user):
            action_label = '拆解回件' if action_type == 'disassemble' else '报废停用'
            _request_high_risk_approval(
                request,
                action_code='unit.dispose',
                module='在外库存',
                target_type='inventory_unit',
                target_id=unit.id,
                target_label=unit.unit_no,
                summary=f'{action_label}审批：{unit.unit_no}',
                payload={
                    'unit_id': unit.id,
                    'unit_no': unit.unit_no,
                    'action_type': action_type,
                    'issue_desc': issue_desc,
                    'notes': notes,
                },
            )
        else:
            messages.error(request, '您没有执行此操作的权限（unit.dispose）')
    except Exception as e:
        messages.error(request, f'单套处置失败：{str(e)}')
    next_url = request.POST.get('next') or reverse('outbound_inventory_dashboard')
    return redirect(next_url)


@login_required
@require_permission('outbound_inventory', 'view')
def unit_disposal_orders_list(request):
    """单套处置工单列表"""
    orders = UnitDisposalOrder.objects.select_related(
        'unit', 'sku', 'created_by', 'completed_by'
    ).prefetch_related('items__part').order_by('-created_at')
    keyword = (request.GET.get('keyword', '') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    action_filter = (request.GET.get('action_type') or '').strip()
    if keyword:
        orders = orders.filter(
            Q(disposal_no__icontains=keyword) |
            Q(unit__unit_no__icontains=keyword) |
            Q(sku__code__icontains=keyword) |
            Q(sku__name__icontains=keyword) |
            Q(unit__current_order__order_no__icontains=keyword) |
            Q(issue_desc__icontains=keyword) |
            Q(created_by__username__icontains=keyword) |
            Q(items__part__name__icontains=keyword)
        ).distinct()
    if status_filter:
        orders = orders.filter(status=status_filter)
    if action_filter:
        orders = orders.filter(action_type=action_filter)
    page = Paginator(orders, _get_page_size('page_size_disposal_orders')).get_page(request.GET.get('page'))
    for order in page.object_list:
        order.items_summary = '，'.join([
            f"{item.part.name} 回收{item.returned_quantity}/{item.quantity}" for item in order.items.all()[:4]
        ]) or '-'
    context = {
        'disposal_orders': page,
        'disposal_orders_page': page,
        'keyword': keyword,
        'status_filter': status_filter,
        'action_filter': action_filter,
        'status_choices': UnitDisposalOrder.STATUS_CHOICES,
        'action_choices': UnitDisposalOrder.ACTION_CHOICES,
        'pagination_query': _build_querystring(request, ['page']),
        'summary': {
            'total_count': orders.count(),
            'disassemble_count': orders.filter(action_type='disassemble').count(),
            'scrap_count': orders.filter(action_type='scrap').count(),
            'completed_count': orders.filter(status='completed').count(),
        },
    }
    return render(request, 'procurement/unit_disposal_orders.html', context)


@login_required
@require_permission('outbound_inventory', 'view')
def unit_disposal_orders_export(request):
    """导出单套处置工单CSV"""
    orders = UnitDisposalOrder.objects.select_related(
        'unit', 'sku', 'created_by', 'completed_by'
    ).prefetch_related('items__part').order_by('-created_at')
    keyword = (request.GET.get('keyword', '') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    action_filter = (request.GET.get('action_type') or '').strip()
    if keyword:
        orders = orders.filter(
            Q(disposal_no__icontains=keyword) |
            Q(unit__unit_no__icontains=keyword) |
            Q(sku__code__icontains=keyword) |
            Q(sku__name__icontains=keyword) |
            Q(unit__current_order__order_no__icontains=keyword) |
            Q(issue_desc__icontains=keyword) |
            Q(created_by__username__icontains=keyword) |
            Q(items__part__name__icontains=keyword)
        ).distinct()
    if status_filter:
        orders = orders.filter(status=status_filter)
    if action_filter:
        orders = orders.filter(action_type=action_filter)

    resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    resp['Content-Disposition'] = 'attachment; filename="unit_disposal_orders.csv"'
    writer = csv.writer(resp)
    writer.writerow([
        '工单号', '动作', '单套编号', 'SKU编码', 'SKU名称', '部件明细',
        '原因说明', '状态', '创建人', '创建时间', '完成人', '完成时间'
    ])
    for order in orders:
        items_summary = '，'.join([
            f"{item.part.name} 回收{item.returned_quantity}/{item.quantity}" for item in order.items.all()
        ]) or '-'
        writer.writerow([
            order.disposal_no,
            order.get_action_type_display(),
            order.unit.unit_no if order.unit else '',
            order.sku.code if order.sku else '',
            order.sku.name if order.sku else '',
            items_summary,
            order.issue_desc or '',
            order.get_status_display(),
            order.created_by.username if order.created_by else '',
            order.created_at.strftime('%Y-%m-%d %H:%M:%S') if order.created_at else '',
            order.completed_by.username if order.completed_by else '',
            order.completed_at.strftime('%Y-%m-%d %H:%M:%S') if order.completed_at else '',
        ])
    return resp


@login_required
@require_permission('outbound_inventory', 'view')
def outbound_inventory_export(request):
    """导出在外库存明细"""
    units = InventoryUnit.objects.select_related('sku', 'current_order').filter(is_active=True).order_by('sku__code', 'unit_no')
    status = (request.GET.get('status') or '').strip()
    sku_id = (request.GET.get('sku_id') or '').strip()
    keyword = (request.GET.get('keyword') or '').strip()
    anomaly_only = (request.GET.get('anomaly_only') or '').strip() == '1'
    settings = get_system_settings()
    warn_outbound_days = int(settings.get('outbound_max_days_warn', 10) or 10)
    warn_hops = int(settings.get('outbound_max_hops_warn', 4) or 4)
    pending_timeout_hours = int(settings.get('transfer_pending_timeout_hours', 24) or 24)
    shipped_timeout_days = int(settings.get('transfer_shipped_timeout_days', 3) or 3)
    now = timezone.now()
    if status:
        units = units.filter(status=status)
    if sku_id.isdigit():
        units = units.filter(sku_id=int(sku_id))
    if keyword:
        units = units.filter(
            Q(unit_no__icontains=keyword)
            | Q(sku__code__icontains=keyword)
            | Q(sku__name__icontains=keyword)
            | Q(current_order__order_no__icontains=keyword)
            | Q(last_tracking_no__icontains=keyword)
        )

    resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    resp['Content-Disposition'] = 'attachment; filename="outbound_inventory_units.csv"'
    writer = csv.writer(resp)
    writer.writerow([
        '单套编号', 'SKU编码', 'SKU名称', '状态', '当前订单', '最近物流单号', '最近节点', '最近节点时间',
        '在途天数', '转寄节点数', '健康分', '健康等级', '预警', '部件总项', '缺件', '损坏', '丢失', '部件异常总数', '拓扑链路'
    ])

    unit_ids = list(units.values_list('id', flat=True))
    latest_map = {}
    hop_count_map = {
        row['unit_id']: int(row['cnt'] or 0)
        for row in UnitMovement.objects.filter(
            unit_id__in=unit_ids,
            event_type__in=['TRANSFER_SHIPPED', 'TRANSFER_COMPLETED']
        ).values('unit_id').annotate(cnt=Count('id'))
    }
    chain_map = defaultdict(list)
    if unit_ids:
        move_qs = UnitMovement.objects.filter(unit_id__in=unit_ids).order_by('unit_id', '-event_time')
        for mv in move_qs:
            if mv.unit_id not in latest_map:
                latest_map[mv.unit_id] = mv
        for mv in UnitMovement.objects.filter(unit_id__in=unit_ids).select_related('from_order', 'to_order').order_by('unit_id', 'event_time'):
            label = dict(UnitMovement.EVENT_CHOICES).get(mv.event_type, mv.event_type)
            if mv.from_order and mv.to_order:
                chain_map[mv.unit_id].append(f"{label}({mv.from_order.order_no}->{mv.to_order.order_no})")
            elif mv.to_order:
                chain_map[mv.unit_id].append(f"{label}(->{mv.to_order.order_no})")
            elif mv.from_order:
                chain_map[mv.unit_id].append(f"{label}({mv.from_order.order_no}->)")
            else:
                chain_map[mv.unit_id].append(label)

    part_summary_map = defaultdict(lambda: {'total': 0, 'missing': 0, 'damaged': 0, 'lost': 0, 'issue': 0})
    for row in InventoryUnitPart.objects.filter(unit_id__in=unit_ids, is_active=True).values('unit_id', 'status'):
        summary = part_summary_map[row['unit_id']]
        summary['total'] += 1
        if row['status'] in ['missing', 'damaged', 'lost']:
            summary[row['status']] += 1
            summary['issue'] += 1

    for unit in units:
        latest = latest_map.get(unit.id)
        hop_count = hop_count_map.get(unit.id, 0)
        outbound_days = (now - latest.event_time).days if (latest and unit.status != 'in_warehouse') else 0
        warn_reason = ''
        if hop_count > warn_hops:
            warn_reason = f'转寄节点>{warn_hops}'
        if outbound_days > warn_outbound_days:
            warn_reason = (warn_reason + '；' if warn_reason else '') + f'在途>{warn_outbound_days}天'
        if latest:
            if latest.event_type == 'TRANSFER_PENDING' and (now - latest.event_time).total_seconds() > pending_timeout_hours * 3600:
                warn_reason = (warn_reason + '；' if warn_reason else '') + f'待执行>{pending_timeout_hours}小时'
            if latest.event_type == 'TRANSFER_SHIPPED' and (now - latest.event_time).days > shipped_timeout_days:
                warn_reason = (warn_reason + '；' if warn_reason else '') + f'转寄在途>{shipped_timeout_days}天'
            if latest.event_type == 'EXCEPTION':
                warn_reason = (warn_reason + '；' if warn_reason else '') + '异常节点'
        part_summary = part_summary_map.get(unit.id, {'total': 0, 'missing': 0, 'damaged': 0, 'lost': 0, 'issue': 0})
        if part_summary['issue'] > 0:
            warn_reason = (warn_reason + '；' if warn_reason else '') + f"部件异常{part_summary['issue']}项"

        if anomaly_only and not warn_reason:
            continue
        health_score, health_level = _compute_unit_health(
            unit_status=unit.status,
            hop_count=hop_count,
            outbound_days=outbound_days,
            warn_reason=warn_reason,
            latest_event_type=(latest.event_type if latest else ''),
        )

        writer.writerow([
            unit.unit_no,
            unit.sku.code if unit.sku else '',
            unit.sku.name if unit.sku else '',
            _get_unit_status_display(unit.status),
            unit.current_order.order_no if unit.current_order else '',
            unit.last_tracking_no or '',
            dict(UnitMovement.EVENT_CHOICES).get(latest.event_type, latest.event_type) if latest else '',
            latest.event_time.strftime('%Y-%m-%d %H:%M:%S') if latest else '',
            outbound_days,
            hop_count,
            health_score,
            health_level,
            warn_reason,
            part_summary['total'],
            part_summary['missing'],
            part_summary['damaged'],
            part_summary['lost'],
            part_summary['issue'],
            " -> ".join(chain_map.get(unit.id, [])),
        ])
    return resp


@login_required
@require_permission('outbound_inventory', 'view')
def outbound_inventory_topology_export(request):
    """导出拓扑链路CSV（按SKU/异常池筛选）"""
    sku_id = (request.GET.get('topology_sku_id') or request.GET.get('sku_id') or '').strip()
    anomaly_only = (request.GET.get('anomaly_only') or '').strip() == '1'
    settings = get_system_settings()
    warn_outbound_days = int(settings.get('outbound_max_days_warn', 10) or 10)
    warn_hops = int(settings.get('outbound_max_hops_warn', 4) or 4)
    now = timezone.now()

    units = InventoryUnit.objects.select_related('sku', 'current_order').filter(is_active=True).order_by('sku__code', 'unit_no')
    if sku_id.isdigit():
        units = units.filter(sku_id=int(sku_id))

    unit_ids = list(units.values_list('id', flat=True))
    latest_map = {}
    hop_count_map = {
        row['unit_id']: int(row['cnt'] or 0)
        for row in UnitMovement.objects.filter(
            unit_id__in=unit_ids,
            event_type__in=['TRANSFER_SHIPPED', 'TRANSFER_COMPLETED']
        ).values('unit_id').annotate(cnt=Count('id'))
    }
    if unit_ids:
        for mv in UnitMovement.objects.filter(unit_id__in=unit_ids).order_by('unit_id', '-event_time'):
            if mv.unit_id not in latest_map:
                latest_map[mv.unit_id] = mv
    part_issue_by_unit = {
        row['unit_id']: int(row['cnt'] or 0)
        for row in InventoryUnitPart.objects.filter(
            unit_id__in=unit_ids,
            is_active=True,
            status__in=['missing', 'damaged', 'lost']
        ).values('unit_id').annotate(cnt=Count('id'))
    }

    resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    resp['Content-Disposition'] = 'attachment; filename="outbound_inventory_topology.csv"'
    writer = csv.writer(resp)
    writer.writerow(['单套编号', 'SKU编码', 'SKU名称', '当前状态', '当前订单', '转寄节点数', '在途天数', '预警', '拓扑链路'])

    for unit in units:
        latest = latest_map.get(unit.id)
        hop_count = hop_count_map.get(unit.id, 0)
        outbound_days = (now - latest.event_time).days if (latest and unit.status != 'in_warehouse') else 0
        warn_reason = ''
        if hop_count > warn_hops:
            warn_reason = f'转寄节点>{warn_hops}'
        if outbound_days > warn_outbound_days:
            warn_reason = (warn_reason + '；' if warn_reason else '') + f'在途>{warn_outbound_days}天'
        if latest and latest.event_type == 'EXCEPTION':
            warn_reason = (warn_reason + '；' if warn_reason else '') + '异常节点'
        part_issue_cnt = int(part_issue_by_unit.get(unit.id, 0) or 0)
        if part_issue_cnt > 0:
            warn_reason = (warn_reason + '；' if warn_reason else '') + f'部件异常{part_issue_cnt}项'
        if anomaly_only and not warn_reason:
            continue
        writer.writerow([
            unit.unit_no,
            unit.sku.code if unit.sku else '',
            unit.sku.name if unit.sku else '',
            _get_unit_status_display(unit.status),
            unit.current_order.order_no if unit.current_order else '',
            hop_count,
            outbound_days,
            warn_reason,
            _build_unit_chain_text(unit),
        ])

    return resp


@login_required
@require_permission('skus', 'view')
def skus_list(request):
    """产品管理"""
    skus = SKU.objects.filter(is_active=True).prefetch_related('components__part', 'images').order_by('code')
    keyword = (request.GET.get('keyword', '') or '').strip()
    category = (request.GET.get('category', '') or '').strip()
    if category:
        skus = skus.filter(category__icontains=category)
    if keyword:
        skus = skus.filter(
            Q(code__icontains=keyword) |
            Q(name__icontains=keyword) |
            Q(category__icontains=keyword) |
            Q(description__icontains=keyword)
        )

    skus_page = Paginator(skus, _get_page_size('page_size_skus')).get_page(request.GET.get('page'))
    sku_components_map = {}
    for sku in skus_page.object_list:
        components = []
        for comp in sku.components.all():
            part_label = comp.part.name
            if comp.part.spec:
                part_label = f"{part_label}（{comp.part.spec}）"
            components.append({
                'part_id': comp.part_id,
                'part_name': comp.part.name,
                'part_spec': comp.part.spec or '',
                'part_label': part_label,
                'quantity_per_set': int(comp.quantity_per_set or 1),
                'notes': comp.notes or '',
            })
        sku.component_count = len(components)
        sku.component_preview = '，'.join([
            f"{item['part_name']}x{item['quantity_per_set']}" for item in components[:3]
        ]) if components else '-'
        sku.components_json = json.dumps(components, ensure_ascii=False)
        sku.components_b64 = base64.b64encode(
            sku.components_json.encode('utf-8')
        ).decode('ascii')
        sku.gallery_json = json.dumps(_build_sku_gallery_snapshot(sku), ensure_ascii=False)
        sku.gallery_b64 = base64.b64encode(
            sku.gallery_json.encode('utf-8')
        ).decode('ascii')
        sku_components_map[str(sku.id)] = components

    parts = Part.objects.filter(is_active=True).order_by('name')
    assembly_feedback = request.session.pop('sku_assembly_feedback', None)
    context = {
        'skus': skus_page,
        'skus_page': skus_page,
        'keyword': keyword,
        'category': category,
        'parts': parts,
        'storage_status': StorageService.get_storage_status(),
        'sku_components_map_json': json.dumps(sku_components_map, ensure_ascii=False),
        'assembly_feedback_json': json.dumps(assembly_feedback, ensure_ascii=False) if assembly_feedback else '',
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'skus.html', context)


@login_required
@require_permission('skus', 'update')
def sku_upload_token(request):
    """获取 Cloudflare R2 直传上传凭证"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': '仅支持 POST 请求'}, status=405)
    if not has_action_permission(request.user, 'sku.upload_image'):
        return JsonResponse({'success': False, 'message': '无权上传产品图片'}, status=403)
    try:
        filename = (request.POST.get('filename') or '').strip()
        payload = StorageService.get_upload_payload(filename)
        return JsonResponse({'success': True, 'data': payload})
    except ValueError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({'success': False, 'message': f'获取上传凭证失败：{exc}'}, status=500)


@login_required
@require_permission('skus', 'view')
def assembly_orders_list(request):
    """装配单列表"""
    orders = AssemblyOrder.objects.select_related('sku', 'created_by').prefetch_related('items__part').order_by('-created_at')
    keyword = (request.GET.get('keyword', '') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    sku_id = (request.GET.get('sku_id') or '').strip()
    if keyword:
        orders = orders.filter(
            Q(assembly_no__icontains=keyword) |
            Q(sku__code__icontains=keyword) |
            Q(sku__name__icontains=keyword) |
            Q(created_by__username__icontains=keyword) |
            Q(notes__icontains=keyword) |
            Q(items__part__name__icontains=keyword)
        ).distinct()
    if status_filter:
        orders = orders.filter(status=status_filter)
    if sku_id.isdigit():
        orders = orders.filter(sku_id=int(sku_id))

    page = Paginator(orders, _get_page_size('page_size_assembly_orders')).get_page(request.GET.get('page'))
    for order in page.object_list:
        order.parts_summary = '，'.join([
            f"{item.part.name}x{item.deducted_quantity}" for item in order.items.all()[:4]
        ]) or '-'
    context = {
        'assembly_orders': page,
        'assembly_orders_page': page,
        'keyword': keyword,
        'status_filter': status_filter,
        'sku_filter': sku_id,
        'status_choices': AssemblyOrder.STATUS_CHOICES,
        'skus': SKU.objects.filter(is_active=True).order_by('code'),
        'pagination_query': _build_querystring(request, ['page']),
        'summary': {
            'total_count': orders.count(),
            'completed_count': orders.filter(status='completed').count(),
            'cancelled_count': orders.filter(status='cancelled').count(),
            'assembled_sets': orders.filter(status='completed').aggregate(total=Sum('quantity')).get('total') or 0,
        },
    }
    return render(request, 'procurement/assembly_orders.html', context)


@login_required
@require_permission('skus', 'view')
def assembly_orders_export(request):
    """导出装配单CSV"""
    orders = AssemblyOrder.objects.select_related('sku', 'created_by').prefetch_related('items__part').order_by('-created_at')
    keyword = (request.GET.get('keyword', '') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    sku_id = (request.GET.get('sku_id') or '').strip()
    if keyword:
        orders = orders.filter(
            Q(assembly_no__icontains=keyword) |
            Q(sku__code__icontains=keyword) |
            Q(sku__name__icontains=keyword) |
            Q(created_by__username__icontains=keyword) |
            Q(notes__icontains=keyword) |
            Q(items__part__name__icontains=keyword)
        ).distinct()
    if status_filter:
        orders = orders.filter(status=status_filter)
    if sku_id.isdigit():
        orders = orders.filter(sku_id=int(sku_id))

    resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    resp['Content-Disposition'] = 'attachment; filename="assembly_orders.csv"'
    writer = csv.writer(resp)
    writer.writerow([
        '装配单号', 'SKU编码', 'SKU名称', '装配套数', '扣减部件', '状态',
        '创建人', '创建时间', '完成时间', '备注'
    ])
    for order in orders:
        parts_summary = '，'.join([
            f"{item.part.name}x{item.deducted_quantity}" for item in order.items.all()
        ]) or '-'
        writer.writerow([
            order.assembly_no,
            order.sku.code if order.sku else '',
            order.sku.name if order.sku else '',
            order.quantity,
            parts_summary,
            order.get_status_display(),
            order.created_by.username if order.created_by else '',
            order.created_at.strftime('%Y-%m-%d %H:%M:%S') if order.created_at else '',
            order.completed_at.strftime('%Y-%m-%d %H:%M:%S') if order.completed_at else '',
            order.notes or '',
        ])
    return resp


@login_required
@require_permission('skus', 'update')
def assembly_order_cancel(request, assembly_id):
    """取消装配单"""
    if request.method != 'POST':
        return redirect('assembly_orders_list')
    assembly = get_object_or_404(AssemblyOrder.objects.select_related('sku'), id=assembly_id)
    try:
        AssemblyService.cancel_assembly(assembly=assembly, user=request.user)
        messages.success(request, f'装配单 {assembly.assembly_no} 已取消并回滚')
    except Exception as e:
        messages.error(request, f'取消装配单失败：{str(e)}')
    next_url = request.POST.get('next') or reverse('assembly_orders_list')
    return redirect(next_url)


@login_required
@require_permission('skus', 'create')
def sku_create(request):
    """创建产品"""
    if request.method == 'POST':
        try:
            components_payload = _parse_sku_components_post(request)
            gallery_payload = _parse_sku_gallery_post(request)
            with transaction.atomic():
                sku = SKU.objects.create(
                    code=request.POST.get('code'),
                    name=request.POST.get('name'),
                    category=request.POST.get('category'),
                    image=request.FILES.get('image'),
                    image_key=(request.POST.get('image_key') or '').strip(),
                    rental_price=request.POST.get('rental_price'),
                    deposit=request.POST.get('deposit'),
                    stock=0,
                    description=request.POST.get('description', ''),
                    mp_visible=request.POST.get('mp_visible') == '1',
                    display_stock=int(request.POST.get('display_stock') or 0),
                    display_stock_warning=int(request.POST.get('display_stock_warning') or 0),
                    mp_sort_order=int(request.POST.get('mp_sort_order') or 0),
                )
                if components_payload:
                    SKUComponent.objects.bulk_create([
                        SKUComponent(
                            sku=sku,
                            part_id=item['part_id'],
                            quantity_per_set=item['quantity_per_set'],
                            notes=item['notes'],
                        ) for item in components_payload
                    ])
                _save_sku_gallery(sku, gallery_payload)
            summary_parts = [f'SKU {sku.code} 创建成功']
            summary_parts.append(f'部件组成已保存：{len(components_payload)} 项')
            summary_parts.append('套餐库存需通过“新增库存（装配）”操作增加')
            messages.success(request, '；'.join(summary_parts))
            return redirect('skus_list')
        except Exception as e:
            messages.error(request, f'SKU创建失败：{str(e)}')

    return redirect('skus_list')


@login_required
@require_permission('skus', 'update')
def sku_edit(request, sku_id):
    """编辑产品"""
    if request.method == 'POST':
        try:
            components_payload = _parse_sku_components_post(request)
            gallery_payload = _parse_sku_gallery_post(request)
            with transaction.atomic():
                sku = get_object_or_404(SKU, id=sku_id)
                sku.code = request.POST.get('code')
                sku.name = request.POST.get('name')
                sku.category = request.POST.get('category')
                new_image = request.FILES.get('image')
                image_key = (request.POST.get('image_key') or '').strip()
                if request.POST.get('clear_image') == '1':
                    sku.image = None
                    sku.image_key = ''
                elif image_key:
                    sku.image = None
                    sku.image_key = image_key
                elif new_image:
                    sku.image = new_image
                    sku.image_key = ''
                sku.rental_price = request.POST.get('rental_price')
                sku.deposit = request.POST.get('deposit')
                sku.description = request.POST.get('description', '')
                sku.mp_visible = request.POST.get('mp_visible') == '1'
                sku.display_stock = int(request.POST.get('display_stock') or 0)
                sku.display_stock_warning = int(request.POST.get('display_stock_warning') or 0)
                sku.mp_sort_order = int(request.POST.get('mp_sort_order') or 0)
                sku.save()
                SKUComponent.objects.filter(sku=sku).delete()
                if components_payload:
                    SKUComponent.objects.bulk_create([
                        SKUComponent(
                            sku=sku,
                            part_id=item['part_id'],
                            quantity_per_set=item['quantity_per_set'],
                            notes=item['notes'],
                        ) for item in components_payload
                    ])
                _save_sku_gallery(sku, gallery_payload)
            InventoryUnitService.sync_unit_parts_for_sku(sku)
            summary_parts = [f'SKU {sku.code} 更新成功']
            summary_parts.append(f'部件组成已更新：{len(components_payload)} 项')
            summary_parts.append('库存变更请使用“新增库存（装配）”，不支持直接手工修改')
            messages.success(request, '；'.join(summary_parts))
            return redirect('skus_list')
        except Exception as e:
            messages.error(request, f'SKU更新失败：{str(e)}')

    return redirect('skus_list')


@login_required
@require_permission('skus', 'update')
def sku_assemble(request, sku_id):
    """通过装配单新增 SKU 库存"""
    if request.method != 'POST':
        return redirect('skus_list')
    sku = get_object_or_404(SKU, id=sku_id, is_active=True)
    try:
        quantity = int((request.POST.get('assembly_quantity') or '0').strip() or '0')
        notes = (request.POST.get('assembly_notes') or '').strip()
        assembly = AssemblyService.create_and_complete_assembly(
            sku=sku,
            quantity=quantity,
            notes=notes,
            user=request.user,
        )
        messages.success(request, f'装配完成：{sku.code} 新增 {assembly.quantity} 套库存')
        messages.info(request, f'装配单号：{assembly.assembly_no}')
        request.session['sku_assembly_feedback'] = {
            'status': 'success',
            'sku_code': sku.code,
            'sku_name': sku.name,
            'quantity': int(assembly.quantity or 0),
            'assembly_no': assembly.assembly_no,
        }
    except Exception as e:
        messages.error(request, f'装配失败：{str(e)}')
        request.session['sku_assembly_feedback'] = {
            'status': 'error',
            'sku_code': sku.code,
            'sku_name': sku.name,
            'quantity': quantity if 'quantity' in locals() else 0,
            'assembly_no': '',
            'error': str(e),
        }
    return redirect('skus_list')


@login_required
@require_permission('skus', 'delete')
def sku_delete(request, sku_id):
    """删除产品"""
    if request.method == 'POST':
        sku = get_object_or_404(SKU, id=sku_id)
        sku_code = sku.code
        sku.is_active = False
        sku.save()
        messages.success(request, f'SKU {sku_code} 已删除')
        return redirect('skus_list')

    return redirect('skus_list')


@login_required
@require_permission('skus', 'delete')
def skus_bulk_delete(request):
    """批量删除产品（逻辑删除）"""
    if request.method != 'POST':
        return redirect('skus_list')
    ids = request.POST.getlist('ids[]') or request.POST.getlist('ids')
    sku_ids = [int(x) for x in ids if str(x).isdigit()]
    if not sku_ids:
        messages.error(request, '请选择要删除的产品')
        return redirect('skus_list')
    updated = SKU.objects.filter(id__in=sku_ids, is_active=True).update(is_active=False)
    if updated:
        messages.success(request, f'批量删除完成：成功 {updated} 条')
    else:
        messages.warning(request, '未删除任何产品（可能已删除）')
    return redirect('skus_list')


@login_required
@require_permission('procurement', 'view')
def purchase_orders_list(request):
    """采购订单列表"""
    pos = PurchaseOrder.objects.select_related('created_by').prefetch_related('items').order_by('-created_at')

    # 筛选
    status_filter = request.GET.get('status', '')
    keyword = (request.GET.get('keyword', '') or '').strip()
    if status_filter:
        pos = pos.filter(status=status_filter)
    if keyword:
        pos = pos.filter(
            Q(po_no__icontains=keyword) |
            Q(supplier__icontains=keyword) |
            Q(link__icontains=keyword)
        )

    purchase_orders_page = Paginator(pos, _get_page_size('page_size_purchase_orders')).get_page(request.GET.get('page'))
    context = {
        'purchase_orders': purchase_orders_page,
        'purchase_orders_page': purchase_orders_page,
        'status_filter': status_filter,
        'keyword': keyword,
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'procurement/purchase_orders.html', context)


@login_required
@require_permission('procurement', 'create')
def purchase_order_create(request):
    """创建采购单"""
    if request.method == 'POST':
        try:
            part_ids = request.POST.getlist('part_id[]')
            quantities = request.POST.getlist('quantity[]')
            unit_prices = request.POST.getlist('unit_price[]')

            items = []
            for part_id, quantity, unit_price in zip(part_ids, quantities, unit_prices):
                if not part_id:
                    continue
                part = get_object_or_404(Part, id=int(part_id))
                items.append({
                    'part_id': part.id,
                    'part_name': part.name,
                    'spec': part.spec,
                    'unit': part.unit,
                    'quantity': int(quantity) if quantity else 1,
                    'unit_price': Decimal(unit_price) if unit_price else Decimal('0.00'),
                })

            if not items:
                messages.error(request, '请至少添加一条采购明细')
                parts = Part.objects.filter(is_active=True)
                return render(request, 'procurement/purchase_order_form.html', {'parts': parts, 'mode': 'create'})

            # 构建采购单数据
            data = {
                'channel': request.POST.get('channel'),
                'supplier': request.POST.get('supplier'),
                'link': request.POST.get('link', ''),
                'order_date': datetime.strptime(request.POST.get('order_date'), '%Y-%m-%d').date(),
                'arrival_date': datetime.strptime(request.POST.get('arrival_date'), '%Y-%m-%d').date() if request.POST.get('arrival_date') else None,
                'notes': request.POST.get('notes', ''),
                'items': items
            }

            # 创建采购单
            po = ProcurementService.create_purchase_order(data, request.user)
            messages.success(request, f'采购单创建成功：{po.po_no}')
            return redirect('purchase_orders_list')

        except Exception as e:
            messages.error(request, f'采购单创建失败：{str(e)}')

    parts = Part.objects.filter(is_active=True)

    context = {
        'parts': parts,
        'mode': 'create',
    }
    return render(request, 'procurement/purchase_order_form.html', context)


@login_required
@require_permission('procurement', 'update')
def purchase_order_edit(request, po_id):
    """编辑采购单"""
    po = get_object_or_404(PurchaseOrder, id=po_id)

    context = {
        'purchase_order': po,
        'mode': 'edit',
    }
    return render(request, 'procurement/purchase_order_form.html', context)


@login_required
@require_permission('procurement', 'delete')
def purchase_order_delete(request, po_id):
    """删除采购单"""
    if request.method == 'POST':
        po = get_object_or_404(PurchaseOrder, id=po_id)

        # 只有草稿状态的采购单可以删除
        if po.status != 'draft':
            messages.error(request, '只有草稿状态的采购单可以删除')
            return redirect('purchase_orders_list')

        po_no = po.po_no
        po.delete()
        messages.success(request, f'采购单 {po_no} 已删除')
        return redirect('purchase_orders_list')

    return redirect('purchase_orders_list')


@login_required
@require_permission('procurement', 'delete')
def purchase_orders_bulk_delete(request):
    """批量删除采购单（仅草稿）"""
    if request.method != 'POST':
        return redirect('purchase_orders_list')
    ids = request.POST.getlist('ids[]') or request.POST.getlist('ids')
    po_ids = [int(x) for x in ids if str(x).isdigit()]
    if not po_ids:
        messages.error(request, '请选择要删除的采购单')
        return redirect('purchase_orders_list')
    qs = PurchaseOrder.objects.filter(id__in=po_ids)
    deleted = 0
    skipped = 0
    for po in qs:
        if po.status != 'draft':
            skipped += 1
            continue
        po.delete()
        deleted += 1
    if deleted:
        messages.success(request, f'批量删除完成：成功 {deleted} 条')
    if skipped:
        messages.warning(request, f'有 {skipped} 条因状态非草稿未删除')
    return redirect('purchase_orders_list')


@login_required
@require_permission('procurement', 'update')
def purchase_order_mark_ordered(request, po_id):
    """采购单：标记已下单"""
    if request.method == 'POST':
        try:
            ProcurementService.mark_as_ordered(po_id, request.user)
            messages.success(request, '采购单已标记为已下单')
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('purchase_orders_list')


@login_required
@require_permission('procurement', 'update')
def purchase_order_mark_arrived(request, po_id):
    """采购单：标记已到货"""
    if request.method == 'POST':
        try:
            ProcurementService.mark_as_arrived(po_id, request.user)
            messages.success(request, '采购单已标记为已到货')
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('purchase_orders_list')


@login_required
@require_permission('procurement', 'update')
def purchase_order_mark_stocked(request, po_id):
    """采购单：标记已入库"""
    if request.method == 'POST':
        try:
            ProcurementService.mark_as_stocked(po_id, request.user)
            messages.success(request, '采购单已入库，部件库存已更新')
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('purchase_orders_list')


@login_required
@require_permission('parts', 'view')
def parts_inventory_list(request):
    """部件库存列表"""
    parts = Part.objects.filter(is_active=True).order_by('name')

    # 筛选
    category = request.GET.get('category', '')
    keyword = (request.GET.get('keyword', '') or '').strip()
    low_only = (request.GET.get('low', '') or '').strip() == '1'
    if category:
        parts = parts.filter(category=category)
    if low_only:
        parts = parts.filter(current_stock__lt=F('safety_stock'))
    if keyword:
        parts = parts.filter(
            Q(name__icontains=keyword) |
            Q(spec__icontains=keyword) |
            Q(location__icontains=keyword)
        )

    parts_page = Paginator(parts, _get_page_size('page_size_parts')).get_page(request.GET.get('page'))
    context = {
        'parts': parts_page,
        'parts_page': parts_page,
        'category': category,
        'keyword': keyword,
        'low_only': low_only,
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'procurement/parts_inventory.html', context)


@login_required
@require_permission('parts', 'create')
def part_create(request):
    """创建部件"""
    if request.method == 'POST':
        try:
            part = Part.objects.create(
                name=request.POST.get('name'),
                spec=request.POST.get('spec', ''),
                category=request.POST.get('category'),
                unit=request.POST.get('unit', '个'),
                current_stock=int(request.POST.get('current_stock', 0)),
                safety_stock=int(request.POST.get('safety_stock', 0)),
                location=request.POST.get('location', ''),
            )
            messages.success(request, f'部件 {part.name} 创建成功')
            return redirect('parts_inventory_list')
        except Exception as e:
            messages.error(request, f'部件创建失败：{str(e)}')

    return redirect('parts_inventory_list')


@login_required
@require_permission('parts', 'update')
def part_edit(request, part_id):
    """编辑部件"""
    if request.method == 'POST':
        try:
            part = get_object_or_404(Part, id=part_id)
            part.name = request.POST.get('name')
            part.spec = request.POST.get('spec', '')
            part.category = request.POST.get('category')
            part.unit = request.POST.get('unit', '个')
            part.safety_stock = int(request.POST.get('safety_stock', 0))
            part.location = request.POST.get('location', '')
            part.save()
            messages.success(request, f'部件 {part.name} 更新成功')
            return redirect('parts_inventory_list')
        except Exception as e:
            messages.error(request, f'部件更新失败：{str(e)}')

    return redirect('parts_inventory_list')


@login_required
@require_permission('parts', 'delete')
def part_delete(request, part_id):
    """删除部件"""
    if request.method == 'POST':
        part = get_object_or_404(Part, id=part_id)

        # 检查是否有库存
        if part.current_stock > 0:
            messages.error(request, '部件还有库存，无法删除')
            return redirect('parts_inventory_list')

        part_name = part.name
        part.is_active = False
        part.save()
        messages.success(request, f'部件 {part_name} 已删除')
        return redirect('parts_inventory_list')

    return redirect('parts_inventory_list')


@login_required
@require_permission('parts', 'delete')
def parts_bulk_delete(request):
    """批量删除部件（仅零库存）"""
    if request.method != 'POST':
        return redirect('parts_inventory_list')
    ids = request.POST.getlist('ids[]') or request.POST.getlist('ids')
    part_ids = [int(x) for x in ids if str(x).isdigit()]
    if not part_ids:
        messages.error(request, '请选择要删除的部件')
        return redirect('parts_inventory_list')
    qs = Part.objects.filter(id__in=part_ids, is_active=True)
    deleted = 0
    skipped = 0
    for part in qs:
        if part.current_stock > 0:
            skipped += 1
            continue
        part.is_active = False
        part.save(update_fields=['is_active'])
        deleted += 1
    if deleted:
        messages.success(request, f'批量删除完成：成功 {deleted} 条')
    if skipped:
        messages.warning(request, f'有 {skipped} 条因库存大于 0 未删除')
    return redirect('parts_inventory_list')


@login_required
@require_permission('parts', 'update')
def part_inbound(request):
    """部件入库"""
    if request.method == 'POST':
        try:
            part_id = request.POST.get('part_id')
            quantity = int(request.POST.get('quantity'))
            related_doc = request.POST.get('related_doc', '')
            notes = request.POST.get('notes', '')

            PartsService.inbound(part_id, quantity, related_doc, notes, request.user)
            messages.success(request, '部件入库成功')
        except Exception as e:
            messages.error(request, f'部件入库失败：{str(e)}')

    return redirect('parts_inventory_list')


@login_required
@require_permission('parts', 'update')
def part_outbound(request):
    """部件出库"""
    if request.method == 'POST':
        try:
            part_id = request.POST.get('part_id')
            quantity = int(request.POST.get('quantity'))
            related_doc = request.POST.get('related_doc', '')
            notes = request.POST.get('notes', '')

            PartsService.outbound(part_id, quantity, related_doc, notes, request.user)
            messages.success(request, '部件出库成功')
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f'部件出库失败：{str(e)}')

    return redirect('parts_inventory_list')


@login_required
@require_permission('parts', 'view')
def parts_movements_list(request):
    """部件出入库流水"""
    movements = PartsMovement.objects.select_related('part', 'operator').order_by('-created_at')
    type_filter = (request.GET.get('type', '') or '').strip()
    keyword = (request.GET.get('keyword', '') or '').strip()
    if type_filter:
        movements = movements.filter(type=type_filter)
    if keyword:
        movements = movements.filter(
            Q(part__name__icontains=keyword) |
            Q(related_doc__icontains=keyword) |
            Q(notes__icontains=keyword) |
            Q(operator__username__icontains=keyword) |
            Q(operator__full_name__icontains=keyword)
        )
    movements_page = Paginator(movements, _get_page_size('page_size_parts_movements')).get_page(request.GET.get('page'))

    context = {
        'movements': movements_page,
        'movements_page': movements_page,
        'type_filter': type_filter,
        'keyword': keyword,
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'procurement/parts_movements.html', context)


@login_required
@require_permission('settings', 'view')
def settings_view(request):
    """系统设置"""
    allowed_tabs = {'order', 'transfer', 'warehouse', 'system'}
    active_tab = (request.GET.get('tab') or 'order').strip()
    if active_tab not in allowed_tabs:
        active_tab = 'order'

    if request.method == 'POST':
        active_tab = (request.POST.get('active_tab') or active_tab).strip()
        if active_tab not in allowed_tabs:
            active_tab = 'order'
        if request.POST.get('run_consistency_check') == '1':
            result = run_data_consistency_checks()
            persist_data_consistency_check_result(result, executed_by=request.user, source='settings')
            messages.info(
                request,
                f"巡检完成：共{result['total_issues']}项（错误{result['error_count']}，警告{result['warning_count']}）"
            )
            if result['issues']:
                top_issue = result['issues'][0]
                messages.warning(
                    request,
                    f"首条问题：[{top_issue.get('severity')}] {top_issue.get('message')}"
                )
            return redirect(f"{reverse('settings')}?tab={active_tab}")
        if request.POST.get('test_alert_notify') == '1':
            current_settings = get_system_settings()
            notify_settings = {
                'alert_notify_enabled': request.POST.get('alert_notify_enabled', current_settings.get('alert_notify_enabled', 0)),
                'alert_notify_min_severity': request.POST.get('alert_notify_min_severity', current_settings.get('alert_notify_min_severity', 'warning')),
                'alert_notify_webhook_url': request.POST.get('alert_notify_webhook_url', current_settings.get('alert_notify_webhook_url', '')),
            }
            result = NotificationService.notify_alerts(
                title='通知测试',
                alerts=[{
                    'source': 'system',
                    'severity': 'danger',
                    'title': '通知链路测试',
                    'value': 1,
                    'desc': f'设置页手工触发（{timezone.now().strftime("%Y-%m-%d %H:%M:%S")}）',
                }],
                settings=notify_settings,
                source='settings_test',
            )
            status = result.get('status')
            if status == 'success':
                messages.success(request, '通知测试发送成功')
            elif status == 'failed':
                messages.error(request, f"通知测试失败：{result.get('error') or '未知错误'}")
            else:
                messages.info(request, '通知未发送（请检查是否启用通知和最低级别配置）')
            return redirect(f"{reverse('settings')}?tab={active_tab}")
        managed_keys = [
            'ship_lead_days',
            'return_offset_days',
            'reservation_followup_lead_days',
            'buffer_days',
            'max_transfer_gap_days',
            'transfer_score_weight_date',
            'transfer_score_weight_confidence',
            'transfer_score_weight_distance',
            'warehouse_sender_name',
            'warehouse_sender_phone',
            'warehouse_sender_address',
            'transfer_pending_timeout_hours',
            'transfer_shipped_timeout_days',
            'outbound_max_days_warn',
            'outbound_max_hops_warn',
            'approval_pending_warn_hours',
            'approval_required_count_default',
            'approval_required_count_order_force_cancel',
            'approval_required_count_transfer_cancel_task',
            'approval_required_count_unit_dispose',
            'approval_required_count_unit_disassemble',
            'approval_required_count_unit_scrap',
            'approval_required_count_map',
            'alert_notify_enabled',
            'alert_notify_webhook_url',
            'alert_notify_min_severity',
            'page_size_default',
            'page_size_transfer_candidates',
            'page_size_outbound_topology_units',
        ]
        raw_approval_map = request.POST.get('approval_required_count_map')
        if raw_approval_map is not None:
            parsed_map = {}
            try:
                parsed_map = json.loads(raw_approval_map.strip() or '{}')
                if not isinstance(parsed_map, dict):
                    raise ValueError('审批层级策略映射必须是JSON对象')
                for action_code, level in parsed_map.items():
                    int_level = int(level)
                    if int_level < 1 or int_level > 5:
                        raise ValueError(f'审批层级超出范围：{action_code}={int_level}（允许1-5）')
            except Exception as e:
                messages.error(request, f'审批层级策略映射配置无效：{e}')
                return redirect(f"{reverse('settings')}?tab={active_tab}")
        before_settings = {
            key: (SystemSettings.objects.filter(key=key).values_list('value', flat=True).first() or '')
            for key in managed_keys
        }
        # 更新设置
        for key in managed_keys:
            value = request.POST.get(key)
            if value is not None:
                SystemSettings.objects.update_or_create(
                    key=key,
                    defaults={'value': value}
                )
        after_settings = {
            key: (SystemSettings.objects.filter(key=key).values_list('value', flat=True).first() or '')
            for key in managed_keys
        }
        AuditService.log_with_diff(
            user=request.user,
            action='update',
            module='系统设置',
            target='settings',
            summary='保存系统设置',
            before=before_settings,
            after=after_settings,
            extra={'active_tab': active_tab},
        )
        messages.success(request, '设置保存成功')
        return redirect(f"{reverse('settings')}?tab={active_tab}")

    # 获取设置
    settings = {}
    for setting in SystemSettings.objects.all():
        settings[setting.key] = setting.value

    context = {
        'settings': settings,
        'active_tab': active_tab,
        'storage_status': StorageService.get_storage_status(),
        'recent_consistency_runs': list(
            DataConsistencyCheckRun.objects.select_related('executed_by').all()[:10]
        ),
    }
    return render(request, 'settings.html', context)


@login_required
@require_permission('risk_events', 'view')
def risk_events_list(request):
    """风险事件列表"""
    status_filter = (request.GET.get('status') or '').strip()
    level_filter = (request.GET.get('level') or '').strip()
    keyword = (request.GET.get('keyword') or '').strip()
    assignee_filter = (request.GET.get('assignee') or '').strip()
    mine_only = (request.GET.get('mine_only') or '').strip() == '1'

    events = RiskEvent.objects.select_related('order', 'transfer', 'detected_by', 'assignee', 'resolved_by').order_by('-created_at')
    if status_filter:
        events = events.filter(status=status_filter)
    if level_filter:
        events = events.filter(level=level_filter)
    if assignee_filter:
        events = events.filter(assignee_id=assignee_filter)
    if mine_only:
        events = events.filter(assignee=request.user)
    if keyword:
        events = events.filter(
            Q(title__icontains=keyword) |
            Q(description__icontains=keyword) |
            Q(order__order_no__icontains=keyword)
        )
    if request.GET.get('export') == '1':
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = 'attachment; filename="risk_events.csv"'
        writer = csv.writer(response)
        writer.writerow(['ID', '状态', '级别', '类型', '标题', '负责人', '关联订单', '关联转寄任务', '触发时间', '处理备注'])
        for e in events:
            writer.writerow([
                e.id,
                e.get_status_display(),
                e.get_level_display(),
                e.get_event_type_display(),
                e.title,
                (e.assignee.full_name if e.assignee and e.assignee.full_name else (e.assignee.username if e.assignee else '')),
                e.order.order_no if e.order else '',
                f'#{e.transfer.id}' if e.transfer else '',
                e.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                (e.processing_note or '').replace('\r', ' ').replace('\n', ' '),
            ])
        return response
    events_page = Paginator(events, _get_page_size('page_size_risk_events')).get_page(request.GET.get('page'))

    context = {
        'events': events_page,
        'events_page': events_page,
        'status_filter': status_filter,
        'level_filter': level_filter,
        'keyword': keyword,
        'assignee_filter': assignee_filter,
        'mine_only': mine_only,
        'assignees': User.objects.filter(is_active=True).order_by('username'),
        'open_count': RiskEvent.objects.filter(status='open').count(),
        'processing_count': RiskEvent.objects.filter(status='processing').count(),
        'closed_count': RiskEvent.objects.filter(status='closed').count(),
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'risk_events.html', context)


@login_required
@require_permission('risk_events', 'update')
def risk_event_resolve(request, event_id):
    """关闭风险事件"""
    if request.method != 'POST':
        return redirect('risk_events_list')
    if not has_action_permission(request.user, 'risk.resolve_event'):
        messages.error(request, '您没有执行此操作的权限（risk.resolve_event）')
        return redirect('risk_events_list')

    event = get_object_or_404(RiskEvent, id=event_id)
    before = {
        'status': event.status,
        'resolved_by_id': event.resolved_by_id,
        'resolved_at': str(event.resolved_at) if event.resolved_at else '',
    }
    note = (request.POST.get('note') or '').strip()
    RiskEventService.resolve_event(event, request.user, note=note)
    AuditService.log_with_diff(
        user=request.user,
        action='status_change',
        module='风险事件',
        target=f'风险事件#{event.id}',
        summary='关闭风险事件',
        before=before,
        after={
            'status': event.status,
            'resolved_by_id': event.resolved_by_id,
            'resolved_at': str(event.resolved_at) if event.resolved_at else '',
        },
        extra={'note': note},
    )
    messages.success(request, '风险事件已关闭')
    return redirect('risk_events_list')


@login_required
@require_permission('risk_events', 'update')
def risk_event_claim(request, event_id):
    """认领风险事件并标记处理中"""
    if request.method != 'POST':
        return redirect('risk_events_list')
    if not has_action_permission(request.user, 'risk.resolve_event'):
        messages.error(request, '您没有执行此操作的权限（risk.resolve_event）')
        return redirect('risk_events_list')

    event = get_object_or_404(RiskEvent, id=event_id)
    if event.status == 'closed':
        messages.warning(request, '该风险事件已关闭，无法认领')
        return redirect('risk_events_list')

    before = {
        'status': event.status,
        'assignee_id': event.assignee_id,
        'processing_note': event.processing_note or '',
    }
    note = (request.POST.get('note') or '').strip()
    RiskEventService.claim_event(event, request.user, note=note)
    AuditService.log_with_diff(
        user=request.user,
        action='update',
        module='风险事件',
        target=f'风险事件#{event.id}',
        summary='认领风险事件',
        before=before,
        after={
            'status': event.status,
            'assignee_id': event.assignee_id,
            'processing_note': event.processing_note or '',
        },
        extra={'note': note},
    )
    messages.success(request, '风险事件已认领并标记为处理中')
    return redirect('risk_events_list')


@login_required
@require_permission('approvals', 'view')
def approvals_list(request):
    """审批中心"""
    tasks = ApprovalTask.objects.select_related('requested_by', 'reviewed_by').all()
    status_filter = (request.GET.get('status', '') or '').strip()
    action_filter = (request.GET.get('action_code', '') or '').strip()
    keyword = (request.GET.get('keyword', '') or '').strip()
    overdue_only = (request.GET.get('overdue_only') or '').strip() == '1'
    mine_only = (request.GET.get('mine_only') or '').strip() == '1'
    reviewable_only = (request.GET.get('reviewable_only') or '').strip() == '1'

    if request.user.role == 'warehouse_manager' and not request.user.is_superuser:
        tasks = tasks.filter(requested_by=request.user)

    if status_filter:
        tasks = tasks.filter(status=status_filter)
    if action_filter:
        tasks = tasks.filter(action_code=action_filter)
    if keyword:
        tasks = tasks.filter(
            Q(task_no__icontains=keyword) |
            Q(target_label__icontains=keyword) |
            Q(summary__icontains=keyword)
        )
    if mine_only:
        tasks = tasks.filter(requested_by=request.user)
    if reviewable_only:
        tasks = tasks.filter(status='pending').exclude(requested_by=request.user)

    approval_warn_hours = int(get_system_settings().get('approval_pending_warn_hours', 24) or 24)
    overdue_cutoff = timezone.now() - timedelta(hours=approval_warn_hours)
    if overdue_only:
        tasks = tasks.filter(status='pending', created_at__lt=overdue_cutoff)

    if request.GET.get('export') == '1':
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = 'attachment; filename="approvals.csv"'
        writer = csv.writer(response)
        writer.writerow(['审批单号', '动作', '目标', '摘要', '申请人', '状态', '进度', '申请时间', '最近催办时间', '催办次数'])
        for t in tasks.order_by('-created_at'):
            required = max(int(t.required_review_count or 1), 1)
            current = int(t.current_review_count or 0)
            writer.writerow([
                t.task_no,
                t.action_code,
                t.target_label or '',
                t.summary,
                t.requested_by.username if t.requested_by else '',
                t.get_status_display(),
                f'{min(current, required)}/{required}',
                t.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                t.last_reminded_at.strftime('%Y-%m-%d %H:%M:%S') if t.last_reminded_at else '',
                int(t.remind_count or 0),
            ])
        return response

    tasks = tasks.order_by('-created_at')
    tasks_page = Paginator(tasks, _get_page_size('page_size_approvals')).get_page(request.GET.get('page'))
    for task in tasks_page.object_list:
        task.is_overdue_pending = task.status == 'pending' and task.created_at < overdue_cutoff
        required = max(int(task.required_review_count or 1), 1)
        current = int(task.current_review_count or 0)
        task.review_progress_text = f'{min(current, required)}/{required}'
    context = {
        'tasks': tasks_page,
        'tasks_page': tasks_page,
        'status_filter': status_filter,
        'action_filter': action_filter,
        'keyword': keyword,
        'overdue_only': overdue_only,
        'mine_only': mine_only,
        'reviewable_only': reviewable_only,
        'approval_warn_hours': approval_warn_hours,
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'approvals.html', context)


@login_required
@require_permission('approvals', 'update')
def approval_task_approve(request, task_id):
    """审批通过并执行高风险动作"""
    if request.method == 'POST':
        if not has_action_permission(request.user, 'approval.review'):
            messages.error(request, '您没有审批权限（approval.review）')
            return redirect('approvals_list')
        try:
            with transaction.atomic():
                task = get_object_or_404(ApprovalTask.objects.select_for_update(), id=task_id)
                if task.status != 'pending':
                    raise ValueError('该审批单已处理')
                if task.requested_by_id == request.user.id:
                    raise ValueError('不能审批自己提交的申请')
                reviewed_user_ids = list(task.reviewed_user_ids or [])
                if request.user.id in reviewed_user_ids:
                    raise ValueError('同一审批人不能重复审批同一任务')

                payload = task.payload if isinstance(task.payload, dict) else {}
                note = (request.POST.get('note') or '').strip()
                now = timezone.now()
                required_count = max(int(task.required_review_count or 1), 1)
                current_count = int(task.current_review_count or 0) + 1
                reviewed_user_ids.append(request.user.id)
                trail = list(task.review_trail or [])
                trail.append({
                    'user_id': request.user.id,
                    'username': request.user.username,
                    'reviewed_at': now.isoformat(),
                    'note': note,
                    'stage': current_count,
                })

                task.current_review_count = current_count
                task.reviewed_user_ids = reviewed_user_ids
                task.review_trail = trail
                task.reviewed_by = request.user
                task.review_note = note
                task.reviewed_at = now

                if current_count < required_count:
                    task.save(update_fields=[
                        'current_review_count', 'reviewed_user_ids', 'review_trail',
                        'reviewed_by', 'review_note', 'reviewed_at', 'updated_at'
                    ])
                    AuditService.log_with_diff(
                        user=request.user,
                        action='status_change',
                        module='审批',
                        target=task.task_no,
                        summary='审批通过（未达执行层级）',
                        before={'status': 'pending', 'current_review_count': current_count - 1},
                        after={'status': 'pending', 'current_review_count': current_count, 'required_review_count': required_count},
                        extra={'action_code': task.action_code, 'target': task.target_label, 'note': note},
                    )
                    messages.success(request, f'审批已记录：{task.task_no}（进度 {current_count}/{required_count}）')
                    return redirect('approvals_list')

                if task.action_code == 'order.force_cancel':
                    order_id = int(payload.get('order_id') or task.target_id)
                    order = get_object_or_404(Order, id=order_id)
                    reason = (payload.get('reason') or '审批取消').strip() or '审批取消'
                    _execute_order_cancel(order, reason, request.user)
                elif task.action_code == 'transfer.cancel_task':
                    transfer_id = int(payload.get('transfer_id') or task.target_id)
                    transfer = get_object_or_404(Transfer, id=transfer_id)
                    reason = (payload.get('reason') or '审批取消').strip() or '审批取消'
                    _execute_transfer_cancel(transfer, request.user, reason)
                elif task.action_code == 'unit.dispose':
                    unit_id = int(payload.get('unit_id') or task.target_id)
                    unit = get_object_or_404(InventoryUnit, id=unit_id)
                    UnitDisposalService.create_and_complete(
                        unit=unit,
                        action_type=(payload.get('action_type') or '').strip(),
                        issue_desc=(payload.get('issue_desc') or '').strip(),
                        notes=(payload.get('notes') or '').strip(),
                        user=request.user,
                    )
                else:
                    raise ValueError(f'不支持的审批动作：{task.action_code}')

                task.status = 'executed'
                task.executed_at = now
                task.save(update_fields=[
                    'status', 'current_review_count', 'reviewed_user_ids', 'review_trail',
                    'reviewed_by', 'review_note', 'reviewed_at', 'executed_at', 'updated_at'
                ])
                AuditService.log_with_diff(
                    user=request.user,
                    action='status_change',
                    module='审批',
                    target=task.task_no,
                    summary='审批通过并执行',
                    before={'status': 'pending', 'current_review_count': current_count - 1},
                    after={'status': 'executed', 'current_review_count': current_count, 'required_review_count': required_count},
                    extra={'action_code': task.action_code, 'target': task.target_label, 'note': note},
                )
            messages.success(request, f'审批通过并已执行：{task.task_no}')
        except Exception as e:
            messages.error(request, f'审批失败：{str(e)}')
    return redirect('approvals_list')


@login_required
@require_permission('approvals', 'update')
def approval_task_reject(request, task_id):
    """驳回审批任务"""
    if request.method == 'POST':
        if not has_action_permission(request.user, 'approval.review'):
            messages.error(request, '您没有审批权限（approval.review）')
            return redirect('approvals_list')
        try:
            with transaction.atomic():
                task = get_object_or_404(ApprovalTask.objects.select_for_update(), id=task_id)
                if task.status != 'pending':
                    raise ValueError('该审批单已处理')
                if task.requested_by_id == request.user.id:
                    raise ValueError('不能驳回自己提交的申请')
                note = (request.POST.get('note') or '').strip()
                if not note:
                    note = '审批驳回'
                task.status = 'rejected'
                task.reviewed_by = request.user
                task.review_note = note
                task.reviewed_at = timezone.now()
                task.save(update_fields=['status', 'reviewed_by', 'review_note', 'reviewed_at', 'updated_at'])
                AuditService.log_with_diff(
                    user=request.user,
                    action='status_change',
                    module='审批',
                    target=task.task_no,
                    summary='审批驳回',
                    before={'status': 'pending'},
                    after={'status': 'rejected'},
                    extra={'action_code': task.action_code, 'target': task.target_label, 'note': note},
                )
            messages.success(request, f'审批已驳回：{task.task_no}')
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('approvals_list')


@login_required
@require_permission('approvals', 'update')
def approval_task_remind(request, task_id):
    """审批催办（人工）"""
    if request.method != 'POST':
        return redirect('approvals_list')
    if not has_action_permission(request.user, 'approval.review'):
        messages.error(request, '您没有审批催办权限（approval.review）')
        return redirect('approvals_list')
    try:
        task = ApprovalService.remind_pending_task(task_id)
        AuditService.log_with_diff(
            user=request.user,
            action='status_change',
            module='审批',
            target=task.task_no,
            summary='审批催办',
            before={},
            after={
                'remind_count': int(task.remind_count or 0),
                'last_reminded_at': task.last_reminded_at.isoformat() if task.last_reminded_at else '',
            },
            extra={'source': 'manual', 'action_code': task.action_code},
        )
        messages.success(request, f'催办成功：{task.task_no}')
    except Exception as e:
        messages.error(request, f'催办失败：{str(e)}')
    return redirect('approvals_list')


@login_required
@require_permission('approvals', 'update')
def approval_remind_overdue(request):
    """批量催办超时审批任务（人工触发）"""
    if request.method != 'POST':
        return redirect('approvals_list')
    if not has_action_permission(request.user, 'approval.review'):
        messages.error(request, '您没有审批催办权限（approval.review）')
        return redirect('approvals_list')
    settings = get_system_settings()
    warn_hours = int(settings.get('approval_pending_warn_hours', 24) or 24)
    cutoff = timezone.now() - timedelta(hours=warn_hours)
    task_ids = list(
        ApprovalTask.objects.filter(status='pending', created_at__lt=cutoff)
        .values_list('id', flat=True)[:200]
    )
    success = 0
    for task_id in task_ids:
        try:
            task = ApprovalService.remind_pending_task(task_id)
            success += 1
            AuditService.log_with_diff(
                user=request.user,
                action='status_change',
                module='审批',
                target=task.task_no,
                summary='批量审批催办',
                before={},
                after={
                    'remind_count': int(task.remind_count or 0),
                    'last_reminded_at': task.last_reminded_at.isoformat() if task.last_reminded_at else '',
                },
                extra={'source': 'manual_batch', 'action_code': task.action_code},
            )
        except Exception:
            continue
    messages.success(request, f'批量催办完成：{success}/{len(task_ids)}')
    return redirect('approvals_list')


@login_required
@require_permission('finance', 'view')
def finance_transactions_list(request):
    """财务流水中心"""
    records = FinanceTransaction.objects.select_related('order', 'reservation', 'created_by').all()
    tx_type = (request.GET.get('tx_type', '') or '').strip()
    keyword = (request.GET.get('keyword', '') or '').strip()
    start_date = (request.GET.get('start_date', '') or '').strip()
    end_date = (request.GET.get('end_date', '') or '').strip()
    export_flag = (request.GET.get('export') or '').strip() == '1'

    if tx_type:
        records = records.filter(transaction_type=tx_type)
    if keyword:
        records = records.filter(
            Q(order__order_no__icontains=keyword) |
            Q(order__customer_name__icontains=keyword) |
            Q(order__source_order_no__icontains=keyword) |
            Q(order__return_service_payment_reference__icontains=keyword) |
            Q(reservation__reservation_no__icontains=keyword) |
            Q(reservation__customer_wechat__icontains=keyword) |
            Q(reservation__customer_name__icontains=keyword) |
            Q(reference_no__icontains=keyword) |
            Q(notes__icontains=keyword)
        )
    if start_date:
        records = records.filter(created_at__date__gte=start_date)
    if end_date:
        records = records.filter(created_at__date__lte=end_date)

    records = records.order_by('-created_at', '-id')

    summary_qs = records.values('transaction_type').annotate(total=Sum('amount'))
    summary_map = {row['transaction_type']: row['total'] or Decimal('0.00') for row in summary_qs}

    if export_flag:
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = 'attachment; filename="finance_transactions.csv"'
        writer = csv.writer(response)
        writer.writerow(['时间', '单据类型', '单号', '客户', '交易类型', '金额', '关联单号', '备注', '操作人'])
        tx_display = dict(FinanceTransaction.TYPE_CHOICES)
        for r in records:
            writer.writerow([
                r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                r.subject_type_label,
                r.subject_no,
                r.subject_customer_name,
                tx_display.get(r.transaction_type, r.transaction_type),
                str(r.amount),
                r.reference_no or '',
                r.notes or '',
                (r.created_by.full_name or r.created_by.username) if r.created_by else '',
            ])
        return response

    records_page = Paginator(records, _get_page_size('page_size_finance_records')).get_page(request.GET.get('page'))
    context = {
        'records': records_page,
        'records_page': records_page,
        'tx_type': tx_type,
        'keyword': keyword,
        'start_date': start_date,
        'end_date': end_date,
        'summary_map': summary_map,
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'finance_transactions.html', context)


@login_required
@require_permission('finance', 'view')
def finance_reconciliation(request):
    """财务对账中心（订单维度）"""
    status_filter = (request.GET.get('status', '') or '').strip()
    keyword = (request.GET.get('keyword', '') or '').strip()
    mismatch_only = (request.GET.get('mismatch_only') or '').strip() == '1'
    mismatch_field = (request.GET.get('mismatch_field', '') or '').strip()
    min_diff_amount_raw = (request.GET.get('min_diff_amount', '') or '').strip()
    try:
        min_diff_amount = Decimal(min_diff_amount_raw or '0')
    except Exception:
        min_diff_amount = Decimal('0')
    if min_diff_amount < Decimal('0'):
        min_diff_amount = Decimal('0')
    export_flag = (request.GET.get('export') or '').strip() == '1'

    rows = build_finance_reconciliation_rows(
        status_filter=status_filter,
        keyword=keyword,
        mismatch_only=mismatch_only,
        mismatch_field=mismatch_field,
        min_diff_amount=min_diff_amount,
    )
    mismatch_stats = {
        'total': len(rows),
        'abnormal': sum(1 for r in rows if r.get('has_mismatch')),
        'deposit_count': sum(1 for r in rows if 'deposit' in (r.get('mismatch_fields') or [])),
        'balance_count': sum(1 for r in rows if 'balance' in (r.get('mismatch_fields') or [])),
        'refund_count': sum(1 for r in rows if 'refund' in (r.get('mismatch_fields') or [])),
        'max_abs_diff': max(
            [
                max(
                    abs(r.get('deposit_diff') or Decimal('0.00')),
                    abs(r.get('balance_diff') or Decimal('0.00')),
                    abs(r.get('refund_diff') or Decimal('0.00')),
                )
                for r in rows
            ] + [Decimal('0.00')]
        ),
    }

    if export_flag:
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = 'attachment; filename="finance_reconciliation.csv"'
        writer = csv.writer(response)
        writer.writerow([
            '订单号', '客户', '状态',
            '应收押金', '流水收押金', '押金差异',
            '应收尾款', '流水收尾款', '尾款差异',
            '应退押金', '流水退押金', '退押金差异',
            '扣罚', '是否异常', '差异摘要', '建议', '创建时间',
        ])
        for r in rows:
            diff_fields = []
            if abs(r['deposit_diff']) > Decimal('0.01'):
                diff_fields.append('押金')
            if abs(r['balance_diff']) > Decimal('0.01'):
                diff_fields.append('尾款')
            if abs(r['refund_diff']) > Decimal('0.01'):
                diff_fields.append('退押')
            writer.writerow([
                r['order'].order_no,
                r['order'].customer_name,
                r['order'].get_status_display(),
                str(r['expected_deposit']),
                str(r['tx_deposit_received']),
                str(r['deposit_diff']),
                str(r['expected_balance_received']),
                str(r['tx_balance_received']),
                str(r['balance_diff']),
                str(r['expected_refund']),
                str(r['tx_deposit_refund']),
                str(r['refund_diff']),
                str(r['tx_penalty']),
                '是' if r['has_mismatch'] else '否',
                '、'.join(diff_fields) if diff_fields else '-',
                '；'.join(r.get('suggestions') or []) if r.get('suggestions') else '-',
                r['order'].created_at.strftime('%Y-%m-%d %H:%M:%S') if r['order'].created_at else '',
            ])
        return response

    rows_page = Paginator(rows, _get_page_size('page_size_finance_reconciliation')).get_page(request.GET.get('page'))
    context = {
        'rows': rows_page,
        'rows_page': rows_page,
        'status_filter': status_filter,
        'keyword': keyword,
        'mismatch_only': mismatch_only,
        'mismatch_field': mismatch_field,
        'min_diff_amount': min_diff_amount_raw,
        'pagination_query': _build_querystring(request, ['page']),
        'mismatch_count': sum(1 for r in rows if r['has_mismatch']),
        'mismatch_stats': mismatch_stats,
    }
    return render(request, 'finance_reconciliation.html', context)


@login_required
@require_permission('finance', 'update')
def finance_reconciliation_raise_risk(request, order_id):
    """财务对账异常生成风险事件"""
    if request.method != 'POST':
        return redirect('finance_reconciliation')
    if not has_action_permission(request.user, 'finance.manual_adjust'):
        messages.error(request, '您没有权限执行此操作（finance.manual_adjust）')
        return redirect('finance_reconciliation')
    order = get_object_or_404(Order, id=order_id)
    note = (request.POST.get('note') or '').strip() or '财务对账异常，需人工核对'
    event, created = RiskEventService.create_event(
        event_type='frequent_cancel',
        level='high',
        module='财务',
        title=f'财务对账异常：{order.order_no}',
        description=note,
        event_data={'source': 'finance_reconciliation', 'order_id': order.id},
        order=order,
        detected_by=request.user,
    )
    AuditService.log_with_diff(
        user=request.user,
        action='create',
        module='财务',
        target=order.order_no,
        summary='财务对账异常生成风险事件',
        before={},
        after={'risk_event_id': event.id, 'created': bool(created)},
        extra={'note': note},
    )
    if created:
        messages.success(request, f'已生成风险事件：#{event.id}')
    else:
        messages.info(request, f'已存在未关闭风险事件：#{event.id}')
    return redirect('finance_reconciliation')


@login_required
@require_permission('ops_center', 'view')
def ops_center(request):
    """运维中心：核心告警聚合视图"""
    settings = get_system_settings()
    transfer_pending_timeout_hours = int(settings.get('transfer_pending_timeout_hours', 24) or 24)
    approval_pending_warn_hours = int(settings.get('approval_pending_warn_hours', 24) or 24)
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

    source_filter = (request.GET.get('source') or '').strip()
    severity_filter = (request.GET.get('severity') or '').strip()
    export_flag = (request.GET.get('export') or '').strip() == '1'

    overdue_transfers = Transfer.objects.select_related('order_from', 'order_to', 'sku').filter(
        status='pending',
        created_at__lt=now - timedelta(hours=transfer_pending_timeout_hours),
    ).order_by('created_at')[:20]
    overdue_approvals = ApprovalTask.objects.select_related('requested_by').filter(
        status='pending',
        created_at__lt=now - timedelta(hours=approval_pending_warn_hours),
    ).order_by('created_at')[:20]
    open_risk_events = RiskEvent.objects.select_related('order', 'transfer').filter(status='open').order_by('-created_at')[:20]
    recent_checks = DataConsistencyCheckRun.objects.select_related('executed_by').order_by('-created_at')[:20]

    alerts = []
    if transfer_overdue_count > 0:
        alerts.append({
            'source': 'transfer',
            'severity': 'danger',
            'title': '转寄任务超时',
            'value': transfer_overdue_count,
            'desc': f'待执行超过 {transfer_pending_timeout_hours} 小时',
            'url': reverse('transfers_list') + '?panel=tasks&status=pending',
        })
    if approval_overdue_count > 0:
        alerts.append({
            'source': 'approval',
            'severity': 'danger',
            'title': '审批任务超时',
            'value': approval_overdue_count,
            'desc': f'待审批超过 {approval_pending_warn_hours} 小时',
            'url': reverse('approvals_list') + '?status=pending',
        })
    if open_risk_count > 0:
        alerts.append({
            'source': 'risk',
            'severity': 'warning',
            'title': '待处理风险事件',
            'value': open_risk_count,
            'desc': '风险事件尚未闭环',
            'url': reverse('risk_events_list') + '?status=open',
        })
    if latest_check and latest_check_issues > 0:
        alerts.append({
            'source': 'consistency',
            'severity': 'warning',
            'title': '一致性巡检存在问题',
            'value': latest_check_issues,
            'desc': f'最近巡检时间：{latest_check.created_at.strftime("%Y-%m-%d %H:%M")}',
            'url': reverse('settings') + '?tab=system',
        })
    if finance_mismatch_count > 0:
        alerts.append({
            'source': 'finance',
            'severity': 'warning',
            'title': '财务对账异常',
            'value': finance_mismatch_count,
            'desc': '最近巡检识别到财务差异订单',
            'url': reverse('finance_reconciliation') + '?mismatch_only=1',
        })

    if source_filter:
        alerts = [a for a in alerts if a.get('source') == source_filter]
    if severity_filter:
        alerts = [a for a in alerts if a.get('severity') == severity_filter]

    if export_flag:
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = 'attachment; filename="ops_alerts.csv"'
        writer = csv.writer(response)
        writer.writerow(['来源', '级别', '告警项', '数量', '说明', '处理链接'])
        source_labels = {
            'transfer': '转寄',
            'approval': '审批',
                'risk': '风险事件',
                'consistency': '一致性巡检',
                'finance': '财务',
            }
        severity_labels = {'danger': '高', 'warning': '中', 'info': '低'}
        for alert in alerts:
            writer.writerow([
                source_labels.get(alert.get('source'), alert.get('source') or ''),
                severity_labels.get(alert.get('severity'), alert.get('severity') or ''),
                alert.get('title') or '',
                alert.get('value') or 0,
                alert.get('desc') or '',
                alert.get('url') or '',
            ])
        return response

    context = {
        'alerts': alerts,
        'transfer_overdue_count': transfer_overdue_count,
        'approval_overdue_count': approval_overdue_count,
        'open_risk_count': open_risk_count,
        'latest_check': latest_check,
        'latest_check_issues': latest_check_issues,
        'finance_mismatch_count': finance_mismatch_count,
        'source_filter': source_filter,
        'severity_filter': severity_filter,
        'overdue_transfers': overdue_transfers,
        'overdue_approvals': overdue_approvals,
        'open_risk_events': open_risk_events,
        'recent_checks': recent_checks,
    }
    return render(request, 'ops_center.html', context)


@login_required
@require_permission('audit_logs', 'view')
def audit_logs(request):
    """操作日志"""
    logs = AuditLog.objects.select_related('user').order_by('-created_at')
    action = (request.GET.get('action', '') or '').strip()
    module_filter = (request.GET.get('module', '') or '').strip()
    keyword = (request.GET.get('keyword', '') or '').strip()
    start_date = (request.GET.get('start_date', '') or '').strip()
    end_date = (request.GET.get('end_date', '') or '').strip()
    structured_only = (request.GET.get('structured_only') or '').strip() == '1'
    changed_only = (request.GET.get('changed_only') or '').strip() == '1'
    risk_only = (request.GET.get('risk_only') or '').strip() == '1'
    source_filter = (request.GET.get('source') or '').strip()
    sort_by = (request.GET.get('sort_by') or 'time_desc').strip()
    export_flag = (request.GET.get('export') or '').strip() == '1'
    if action:
        logs = logs.filter(action=action)
    if module_filter:
        logs = logs.filter(module=module_filter)
    if start_date:
        logs = logs.filter(created_at__date__gte=start_date)
    if end_date:
        logs = logs.filter(created_at__date__lte=end_date)
    if keyword:
        logs = logs.filter(
            Q(target__icontains=keyword) |
            Q(details__icontains=keyword) |
            Q(module__icontains=keyword) |
            Q(user__username__icontains=keyword) |
            Q(user__full_name__icontains=keyword)
        )
    if risk_only:
        logs = logs.filter(action__in=['status_change', 'delete'])

    filtered_logs = []
    for log in logs:
        parsed = _parse_audit_details(log)
        log_source = 'plain'
        if parsed.get('is_structured'):
            log_source = (parsed.get('extra', {}) or {}).get('source') or 'app'
        if source_filter and log_source != source_filter:
            continue
        if structured_only and not parsed.get('is_structured'):
            continue
        if changed_only and not parsed.get('changed_fields'):
            continue
        log.details_parsed = parsed
        log.changed_fields_count = len(parsed.get('changed_fields') or [])
        log.audit_source = log_source
        filtered_logs.append(log)

    if sort_by == 'time_asc':
        filtered_logs.sort(key=lambda x: x.created_at)
    elif sort_by == 'changed_desc':
        filtered_logs.sort(key=lambda x: (x.changed_fields_count, x.created_at), reverse=True)
    elif sort_by == 'changed_asc':
        filtered_logs.sort(key=lambda x: (x.changed_fields_count, x.created_at))
    else:
        filtered_logs.sort(key=lambda x: x.created_at, reverse=True)

    summary_stats = {
        'total': len(filtered_logs),
        'structured': sum(1 for l in filtered_logs if l.details_parsed.get('is_structured')),
        'changed': sum(1 for l in filtered_logs if l.details_parsed.get('changed_fields')),
        'risk': sum(1 for l in filtered_logs if l.action in ['status_change', 'delete']),
    }

    if export_flag:
        resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        resp['Content-Disposition'] = 'attachment; filename="audit_logs.csv"'
        writer = csv.writer(resp)
        writer.writerow([
            '时间', '用户', '模块', '来源', '操作类型', '目标',
            '摘要', '变更字段', '变更字段数量',
            'Before(JSON)', 'After(JSON)', 'Extra(JSON)',
            '详情原文'
        ])
        action_display_map = dict(AuditLog.ACTION_CHOICES)
        for log in filtered_logs:
            parsed = log.details_parsed
            user_name = '-'
            if log.user:
                user_name = log.user.full_name or log.user.username or '-'
            before_json = parsed.get('before_pretty', '')
            after_json = parsed.get('after_pretty', '')
            extra_json = parsed.get('extra_pretty', '')
            writer.writerow([
                log.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                user_name,
                log.module or '',
                getattr(log, 'audit_source', 'plain'),
                action_display_map.get(log.action, log.action),
                log.target or '',
                parsed.get('summary', ''),
                ','.join(parsed.get('changed_fields', [])),
                len(parsed.get('changed_fields', [])),
                before_json,
                after_json,
                extra_json,
                log.details or '',
            ])
        return resp

    logs_page = Paginator(filtered_logs, _get_page_size('page_size_audit_logs')).get_page(request.GET.get('page'))

    modules = list(
        AuditLog.objects.exclude(module__isnull=True)
        .exclude(module='')
        .values_list('module', flat=True)
        .distinct()
        .order_by('module')
    )

    context = {
        'logs': logs_page,
        'logs_page': logs_page,
        'action': action,
        'module_filter': module_filter,
        'modules': modules,
        'keyword': keyword,
        'start_date': start_date,
        'end_date': end_date,
        'structured_only': structured_only,
        'changed_only': changed_only,
        'risk_only': risk_only,
        'source_filter': source_filter,
        'sort_by': sort_by,
        'summary_stats': summary_stats,
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'audit_logs.html', context)


@login_required
@require_permission('users', 'view')
def users_list(request):
    """用户管理"""
    users = User.objects.all().order_by('-created_at')
    permission_templates = PermissionTemplate.objects.filter(is_active=True).order_by('name')
    role = (request.GET.get('role', '') or '').strip()
    status = (request.GET.get('status', '') or '').strip()
    keyword = (request.GET.get('keyword', '') or '').strip()
    if role:
        users = users.filter(role=role)
    if status == 'active':
        users = users.filter(is_active=True)
    elif status == 'inactive':
        users = users.filter(is_active=False)
    if keyword:
        users = users.filter(
            Q(username__icontains=keyword) |
            Q(full_name__icontains=keyword) |
            Q(email__icontains=keyword) |
            Q(phone__icontains=keyword)
        )

    users_page = Paginator(users, _get_page_size('page_size_users')).get_page(request.GET.get('page'))
    user_permission_previews = {
        str(user.id): get_user_permission_preview(user)
        for user in users_page.object_list
    }
    recent_permission_audits = AuditLog.objects.filter(
        module__in=['用户管理', '权限模板']
    ).select_related('user').order_by('-created_at')[:20]
    for log in recent_permission_audits:
        log.details_parsed = _parse_audit_details(log)
    context = {
        'users': users_page,
        'users_page': users_page,
        'role_filter': role,
        'status_filter': status,
        'keyword': keyword,
        'pagination_query': _build_querystring(request, ['page']),
        'permission_mode_choices': User.PERMISSION_MODE_CHOICES,
        'permission_modules': list(PERMISSION_MODULE_LABELS.items()),
        'permission_actions': list(PERMISSION_ACTION_LABELS.items()),
        'action_permission_choices': list(ACTION_PERMISSION_LABELS.items()),
        'role_choices': User.ROLE_CHOICES,
        'permission_templates': permission_templates,
        'user_permission_previews': user_permission_previews,
        'recent_permission_audits': recent_permission_audits,
    }
    return render(request, 'users.html', context)


@login_required
@require_permission('users', 'create')
def user_create(request):
    """创建用户"""
    if request.method != 'POST':
        return redirect('users_list')
    try:
        username = (request.POST.get('username') or '').strip()
        full_name = (request.POST.get('full_name') or '').strip()
        role = (request.POST.get('role') or '').strip()
        email = (request.POST.get('email') or '').strip()
        phone = (request.POST.get('phone') or '').strip()
        password = request.POST.get('password') or ''
        permission_payload = _get_user_permission_form_payload(request)

        if not username:
            raise ValueError('用户名不能为空')
        if not full_name:
            raise ValueError('姓名不能为空')
        if role not in dict(User.ROLE_CHOICES):
            raise ValueError('角色无效')
        if len(password) < 6:
            raise ValueError('密码至少 6 位')
        if User.objects.filter(username=username).exists():
            raise ValueError('用户名已存在')

        user = User.objects.create_user(
            username=username,
            password=password,
            full_name=full_name,
            role=role,
            permission_mode=permission_payload['permission_mode'],
            custom_modules=permission_payload['custom_modules'],
            custom_actions=permission_payload['custom_actions'],
            custom_action_permissions=permission_payload['custom_action_permissions'],
            email=email,
            phone=phone,
            is_active=True,
        )
        AuditService.log_with_diff(
            user=request.user,
            action='create',
            module='用户管理',
            target=f'用户:{user.username}',
            summary='创建用户',
            before={},
            after=_snapshot_user_audit(user),
            extra={'source': 'app', 'entity': 'user', 'target_user_id': user.id},
        )
        messages.success(request, f'用户 {user.username} 创建成功')
    except Exception as e:
        messages.error(request, f'用户创建失败：{str(e)}')
    return redirect('users_list')


@login_required
@require_permission('users', 'update')
def user_edit(request, user_id):
    """编辑用户"""
    if request.method != 'POST':
        return redirect('users_list')
    user_obj = get_object_or_404(User, id=user_id)
    before_snapshot = _snapshot_user_audit(user_obj)
    try:
        username = (request.POST.get('username') or '').strip()
        full_name = (request.POST.get('full_name') or '').strip()
        role = (request.POST.get('role') or '').strip()
        email = (request.POST.get('email') or '').strip()
        phone = (request.POST.get('phone') or '').strip()
        password = request.POST.get('password') or ''
        permission_payload = _get_user_permission_form_payload(request)

        if not username:
            raise ValueError('用户名不能为空')
        if not full_name:
            raise ValueError('姓名不能为空')
        if role not in dict(User.ROLE_CHOICES):
            raise ValueError('角色无效')
        if User.objects.exclude(id=user_obj.id).filter(username=username).exists():
            raise ValueError('用户名已存在')
        if password and len(password) < 6:
            raise ValueError('密码至少 6 位')

        user_obj.username = username
        user_obj.full_name = full_name
        user_obj.role = role
        user_obj.permission_mode = permission_payload['permission_mode']
        user_obj.custom_modules = permission_payload['custom_modules']
        user_obj.custom_actions = permission_payload['custom_actions']
        user_obj.custom_action_permissions = permission_payload['custom_action_permissions']
        user_obj.email = email
        user_obj.phone = phone
        user_obj.save(update_fields=[
            'username',
            'full_name',
            'role',
            'permission_mode',
            'custom_modules',
            'custom_actions',
            'custom_action_permissions',
            'email',
            'phone',
            'updated_at',
        ])
        if password:
            user_obj.set_password(password)
            user_obj.save(update_fields=['password'])
        AuditService.log_with_diff(
            user=request.user,
            action='update',
            module='用户管理',
            target=f'用户:{user_obj.username}',
            summary='编辑用户',
            before=before_snapshot,
            after=_snapshot_user_audit(user_obj),
            extra={'source': 'app', 'entity': 'user', 'target_user_id': user_obj.id},
        )
        messages.success(request, f'用户 {user_obj.username} 更新成功')
    except Exception as e:
        messages.error(request, f'用户更新失败：{str(e)}')
    return redirect('users_list')


@login_required
@require_permission('users', 'update')
def user_toggle_status(request, user_id):
    """启用/禁用用户"""
    if request.method != 'POST':
        return redirect('users_list')
    user_obj = get_object_or_404(User, id=user_id)
    before_snapshot = _snapshot_user_audit(user_obj)
    try:
        enable = (request.POST.get('enable') or '').strip() in {'1', 'true', 'True'}
        if user_obj.id == request.user.id and not enable:
            raise ValueError('不能禁用当前登录用户')
        user_obj.is_active = enable
        user_obj.save(update_fields=['is_active'])
        AuditService.log_with_diff(
            user=request.user,
            action='status_change',
            module='用户管理',
            target=f'用户:{user_obj.username}',
            summary=f'用户状态变更为{"启用" if enable else "禁用"}',
            before=before_snapshot,
            after=_snapshot_user_audit(user_obj),
            extra={'source': 'app', 'entity': 'user', 'target_user_id': user_obj.id},
        )
        messages.success(request, f'用户 {user_obj.username} 已{"启用" if enable else "禁用"}')
    except Exception as e:
        messages.error(request, f'用户状态更新失败：{str(e)}')
    return redirect('users_list')


@login_required
@require_permission('users', 'create')
def permission_template_create(request):
    """创建权限模板"""
    if request.method != 'POST':
        return redirect('users_list')
    try:
        payload = _get_permission_template_payload(request)
        if PermissionTemplate.objects.filter(name=payload['name']).exists():
            raise ValueError('模板名称已存在')
        template = PermissionTemplate.objects.create(**payload)
        AuditService.log_with_diff(
            user=request.user,
            action='create',
            module='权限模板',
            target=f'模板:{template.name}',
            summary='创建权限模板',
            before={},
            after=_snapshot_permission_template_audit(template),
            extra={'source': 'app', 'entity': 'permission_template', 'template_id': template.id},
        )
        messages.success(request, f'权限模板 {payload["name"]} 创建成功')
    except Exception as e:
        messages.error(request, f'权限模板创建失败：{str(e)}')
    return redirect('users_list')


@login_required
@require_permission('users', 'update')
def permission_template_edit(request, template_id):
    """编辑权限模板"""
    if request.method != 'POST':
        return redirect('users_list')
    template = get_object_or_404(PermissionTemplate, id=template_id)
    before_snapshot = _snapshot_permission_template_audit(template)
    try:
        payload = _get_permission_template_payload(request)
        if PermissionTemplate.objects.exclude(id=template.id).filter(name=payload['name']).exists():
            raise ValueError('模板名称已存在')
        template.name = payload['name']
        template.base_role = payload['base_role']
        template.description = payload['description']
        template.modules = payload['modules']
        template.actions = payload['actions']
        template.action_permissions = payload['action_permissions']
        template.save(update_fields=['name', 'base_role', 'description', 'modules', 'actions', 'action_permissions', 'updated_at'])
        AuditService.log_with_diff(
            user=request.user,
            action='update',
            module='权限模板',
            target=f'模板:{template.name}',
            summary='编辑权限模板',
            before=before_snapshot,
            after=_snapshot_permission_template_audit(template),
            extra={'source': 'app', 'entity': 'permission_template', 'template_id': template.id},
        )
        messages.success(request, f'权限模板 {template.name} 更新成功')
    except Exception as e:
        messages.error(request, f'权限模板更新失败：{str(e)}')
    return redirect('users_list')


@login_required
@require_permission('users', 'delete')
def permission_template_delete(request, template_id):
    """删除权限模板"""
    if request.method != 'POST':
        return redirect('users_list')
    template = get_object_or_404(PermissionTemplate, id=template_id)
    name = template.name
    before_snapshot = _snapshot_permission_template_audit(template)
    try:
        template.delete()
        AuditService.log_with_diff(
            user=request.user,
            action='delete',
            module='权限模板',
            target=f'模板:{name}',
            summary='删除权限模板',
            before=before_snapshot,
            after={},
            extra={'source': 'app', 'entity': 'permission_template'},
        )
        messages.success(request, f'权限模板 {name} 已删除')
    except Exception as e:
        messages.error(request, f'权限模板删除失败：{str(e)}')
    return redirect('users_list')


@login_required
@require_permission('orders', 'update')
def order_mark_delivered(request, order_id):
    """工作台：标记订单发货"""
    if request.method == 'POST':
        if not has_action_permission(request.user, 'order.confirm_delivery'):
            messages.error(request, '您没有执行此操作的权限（order.confirm_delivery）')
            return redirect('workbench')
        try:
            OrderService.mark_as_delivered(order_id, request.POST.get('ship_tracking', ''), request.user)
            messages.success(request, '订单已标记为已发货')
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('workbench')


@login_required
@require_permission('orders', 'update')
def order_mark_confirmed(request, order_id):
    """工作台：确认订单并进入待发货"""
    if request.method == 'POST':
        try:
            if not has_action_permission(request.user, 'order.confirm_delivery'):
                raise ValueError('您没有执行此操作的权限（order.confirm_delivery）')
            deposit_paid = Decimal(request.POST.get('deposit_paid', '0') or '0')
            auto_deliver = request.POST.get('auto_deliver') == '1'
            ship_tracking = (request.POST.get('ship_tracking', '') or '').strip()
            if auto_deliver and not ship_tracking:
                raise ValueError('快递单号不能为空')

            OrderService.confirm_order(order_id, deposit_paid, request.user)

            if auto_deliver:
                OrderService.mark_as_delivered(order_id, ship_tracking, request.user)
                messages.success(request, '订单已录入运单并标记为已发货')
            else:
                messages.success(request, '订单已确认并进入待发货')
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('workbench')


@login_required
@require_permission('orders', 'update')
def order_mark_returned(request, order_id):
    """工作台：标记归还"""
    if request.method == 'POST':
        if not has_action_permission(request.user, 'order.mark_returned'):
            messages.error(request, '您没有执行此操作的权限（order.mark_returned）')
            return redirect('workbench')
        try:
            order = get_object_or_404(Order, id=order_id)
            if _is_transfer_source_order_active(order):
                raise ValueError('该订单为转寄链路订单，请前往【转寄中心】完成操作')
            return_tracking = request.POST.get('return_tracking', '')
            balance_paid = Decimal(request.POST.get('balance_paid', '0') or '0')
            OrderService.mark_as_returned(order_id, return_tracking, balance_paid, request.user)
            messages.success(request, '订单已标记归还')
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('workbench')


@login_required
@require_permission('orders', 'update')
def order_mark_completed(request, order_id):
    """工作台：标记订单完成（自动执行归还再完成）"""
    if request.method == 'POST':
        try:
            if not has_action_permission(request.user, 'order.mark_returned'):
                raise ValueError('您没有执行此操作的权限（order.mark_returned）')
            order = get_object_or_404(Order, id=order_id)
            if order.status != 'returned':
                raise ValueError(f"订单状态为 {order.get_status_display()}，无法标记完成")
            OrderService.complete_order(order_id, request.user)
            messages.success(request, '订单已标记为已完成')
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('workbench')


@login_required
@require_permission('orders', 'update')
def order_cancel(request, order_id):
    """取消订单"""
    if request.method == 'POST':
        try:
            order = get_object_or_404(Order, id=order_id)
            reason = request.POST.get('reason', '手动取消')
            if order.status not in ['pending', 'confirmed']:
                raise ValueError(f"订单状态为 {order.get_status_display()}，无法取消")
            if has_action_permission(request.user, 'order.force_cancel'):
                _execute_order_cancel(order, reason, request.user)
                messages.success(request, '订单已取消')
            elif can_request_approval(request.user):
                _request_high_risk_approval(
                    request,
                    action_code='order.force_cancel',
                    module='订单',
                    target_type='order',
                    target_id=order.id,
                    target_label=order.order_no,
                    summary=f'申请取消订单 {order.order_no}',
                    payload={
                        'order_id': order.id,
                        'order_no': order.order_no,
                        'reason': reason,
                    },
                )
            else:
                messages.error(request, '您没有执行此操作的权限（order.force_cancel）')
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('orders_list')


# ==================== API接口（用于前端AJAX调用） ====================

@login_required
def api_get_sku_details(request, sku_id):
    """API: 获取SKU详情（用于订单表单）"""
    try:
        sku = SKU.objects.get(id=sku_id, is_active=True)
        return JsonResponse({
            'success': True,
            'data': {
                'id': sku.id,
                'name': sku.name,
                'rental_price': str(sku.rental_price),
                'deposit': str(sku.deposit),
                'stock': sku.effective_stock,
            }
        })
    except SKU.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'SKU不存在'})


@login_required
def api_check_availability(request):
    """API: 检查SKU可用性"""
    from .utils import check_sku_availability

    sku_id = request.GET.get('sku_id')
    event_date = request.GET.get('event_date')
    quantity = int(request.GET.get('quantity', 1))
    rental_days = int(request.GET.get('rental_days', 1))

    try:
        event_date = datetime.strptime(event_date, '%Y-%m-%d').date()
        result = check_sku_availability(sku_id, event_date, quantity, rental_days=rental_days)
        return JsonResponse({
            'success': True,
            'data': result
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})


@login_required
def api_transfer_match(request):
    """API: 创建订单阶段转寄匹配建议"""
    try:
        sku_id = int(request.GET.get('sku_id'))
        event_date = datetime.strptime(request.GET.get('event_date'), '%Y-%m-%d').date()
        quantity = int(request.GET.get('quantity', 1))
        delivery_address = request.GET.get('delivery_address', '')
        preferred_source_order_id_raw = (request.GET.get('preferred_source_order_id') or '').strip()
        preferred_source_order_id = int(preferred_source_order_id_raw) if preferred_source_order_id_raw.isdigit() else None

        plan = build_transfer_allocation_plan(
            delivery_address=delivery_address,
            target_event_date=event_date,
            sku_id=sku_id,
            quantity=quantity,
            preferred_source_order_id=preferred_source_order_id,
        )

        candidates = []
        for c in plan['candidates']:
            candidates.append({
                'source_order_id': c['source_order'].id,
                'source_order_no': c['source_order'].order_no,
                'source_event_date': c['source_order'].event_date.strftime('%Y-%m-%d'),
                'available_qty': c['available_qty'],
                'date_gap_score': c.get('date_gap_score'),
                'buffer_days': c.get('buffer_days'),
                'distance_score': str(c['distance_score']),
                'distance_mode': c.get('distance_mode', 'km'),
                'distance_confidence': c.get('distance_confidence'),
                'source_province': c.get('source_province'),
                'source_city': c.get('source_city'),
                'target_province': c.get('target_province'),
                'target_city': c.get('target_city'),
            })

        return JsonResponse({
            'success': True,
            'data': {
                'warehouse_needed': plan['warehouse_needed'],
                'allocations': [
                    {
                        **a,
                        'source_event_date': a['source_event_date'].strftime('%Y-%m-%d') if a.get('source_event_date') else None,
                    } for a in plan['allocations']
                ],
                'candidates': candidates,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})


@login_required
def api_check_duplicate_order(request):
    """API: 检查潜在重复订单（提醒用途，不拦截）"""
    try:
        customer_phone = request.GET.get('customer_phone', '')
        delivery_address = request.GET.get('delivery_address', '')
        event_date_raw = request.GET.get('event_date')
        exclude_order_id = request.GET.get('exclude_order_id')
        sku_ids_raw = request.GET.getlist('sku_ids[]') or request.GET.getlist('sku_ids') or []
        if not sku_ids_raw:
            sku_ids_raw = [x for x in (request.GET.get('sku_ids', '') or '').split(',') if x]
        sku_ids = [int(x) for x in sku_ids_raw if str(x).isdigit()]
        exclude_id = int(exclude_order_id) if str(exclude_order_id).isdigit() else None
        event_date = datetime.strptime(event_date_raw, '%Y-%m-%d').date() if event_date_raw else None

        duplicates = _find_duplicate_orders(
            customer_phone=customer_phone,
            delivery_address=delivery_address,
            event_date=event_date,
            sku_ids=sku_ids,
            exclude_order_id=exclude_id,
            limit=20,
        )
        status_display_map = dict(Order.STATUS_CHOICES)
        data = []
        for row in duplicates:
            overlap_names = []
            if row['overlap_sku_ids']:
                overlap_names = list(
                    SKU.objects.filter(id__in=row['overlap_sku_ids']).values_list('name', flat=True)
                )
            data.append({
                'id': row['id'],
                'order_no': row['order_no'],
                'customer_name': row['customer_name'],
                'customer_phone': row['customer_phone'],
                'event_date': row['event_date'].strftime('%Y-%m-%d'),
                'status': row['status'],
                'status_display': status_display_map.get(row['status'], row['status']),
                'created_at': row['created_at'].strftime('%Y-%m-%d %H:%M'),
                'delivery_address': row['delivery_address'],
                'overlap_sku_names': overlap_names,
            })

        return JsonResponse({
            'success': True,
            'data': {
                'has_duplicates': len(data) > 0,
                'duplicates': data,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})
