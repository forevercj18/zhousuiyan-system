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
from django.db.models import Q, Count, Sum, F
from django.db import models, transaction
from django.utils import timezone
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import csv
import json

from .models import (
    Order, OrderItem, SKU, Part, PurchaseOrder, PurchaseOrderItem, PartsMovement,
    AuditLog, User, SystemSettings, Transfer, TransferAllocation, InventoryUnit, UnitMovement
)
from .services import OrderService, ProcurementService, PartsService, InventoryUnitService, AuditService
from .permissions import require_permission, filter_queryset_by_permission, has_action_permission
from .utils import (
    get_calendar_data,
    find_transfer_candidates,
    create_transfer_task,
    build_transfer_allocation_plan,
    get_transfer_match_candidates,
    build_transfer_pool_rows,
    sync_transfer_tasks_for_target_order,
    get_system_settings,
    get_dashboard_stats_payload,
    get_role_dashboard_payload,
)


def _normalize_text(value):
    return ''.join((value or '').strip().split()).lower()


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
    role_dashboard = get_role_dashboard_payload(request.user)

    # 最近订单
    recent_orders = Order.objects.select_related('created_by').prefetch_related(
        'items__sku',
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
    }
    return render(request, 'dashboard.html', context)


@login_required
@require_permission('workbench', 'view')
def workbench(request):
    """订单处理入口已合并到订单中心，保留旧路由并跳转。"""
    return redirect('orders_list')


@login_required
@require_permission('orders', 'view')
def orders_list(request):
    """订单列表"""
    # 根据权限过滤订单
    orders = filter_queryset_by_permission(
        Order.objects.select_related('created_by').prefetch_related(
            'items__sku',
            'transfer_allocations_target__source_order',
        ),
        request.user,
        'Order'
    )

    # 筛选
    status_filter = request.GET.get('status', '')
    keyword = request.GET.get('keyword', '')

    if status_filter:
        orders = orders.filter(status=status_filter)

    if keyword:
        orders = orders.filter(
            Q(order_no__icontains=keyword) |
            Q(customer_name__icontains=keyword) |
            Q(customer_phone__icontains=keyword)
        )

    orders = orders.order_by('-created_at')
    orders_page = Paginator(orders, 10).get_page(request.GET.get('page'))
    _attach_transfer_allocations_display(orders_page.object_list)
    current_ids = [o.id for o in orders_page.object_list]
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
    for order in orders_page.object_list:
        order.can_mark_returned_in_orders_center = order.id not in source_blocked_ids
        order.active_as_source_count = source_usage_count_map.get(order.id, 0)
        order.expected_deposit = sum(
            (item.deposit or Decimal('0.00')) * (item.quantity or 0)
            for item in order.items.all()
        )

    context = {
        'orders': orders_page,
        'orders_page': orders_page,
        'status_filter': status_filter,
        'keyword': keyword,
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'orders/list.html', context)


@login_required
@require_permission('orders', 'create')
def order_create(request):
    """创建订单"""
    if request.method == 'POST':
        try:
            # 获取订单明细
            sku_ids = request.POST.getlist('sku_id[]')
            quantities = request.POST.getlist('quantity[]')
            transfer_source_order_ids = request.POST.getlist('transfer_source_order_id[]')

            # 验证至少有一个明细
            if not sku_ids or not sku_ids[0]:
                messages.error(request, '请至少添加一个订单明细')
                skus = SKU.objects.filter(is_active=True)
                return render(request, 'orders/form.html', {'skus': skus, 'mode': 'create'})

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
                'customer_email': request.POST.get('customer_email', ''),
                'delivery_address': request.POST.get('delivery_address'),
                'return_address': request.POST.get('return_address', ''),
                'event_date': datetime.strptime(request.POST.get('event_date'), '%Y-%m-%d').date(),
                'rental_days': int(request.POST.get('rental_days', 1)),
                'notes': request.POST.get('notes', ''),
                'items': items
            }

            # 创建订单
            order = OrderService.create_order(data, request.user)
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

    # 获取可用的SKU
    skus = SKU.objects.filter(is_active=True)

    context = {
        'skus': skus,
        'mode': 'create',
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
                'customer_email': request.POST.get('customer_email', ''),
                'delivery_address': request.POST.get('delivery_address'),
                'return_address': request.POST.get('return_address', ''),
                'event_date': datetime.strptime(request.POST.get('event_date'), '%Y-%m-%d').date(),
                'rental_days': int(request.POST.get('rental_days', 1)),
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
    for item in order.items.all():
        item_rows.append({
            'item': item,
            'allocations': transfer_allocations_by_sku.get(item.sku_id, []),
        })

    context = {
        'order': order,
        'transfer_allocations_by_sku': dict(transfer_allocations_by_sku),
        'target_transfer_allocations': list(allocations_qs),
        'item_rows': item_rows,
    }
    return render(request, 'orders/detail.html', context)


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
@require_permission('calendar', 'view')
def calendar_view(request):
    """排期看板"""
    import json

    # 获取年月参数
    year = int(request.GET.get('year', datetime.now().year))
    month = int(request.GET.get('month', datetime.now().month))

    # 获取当月的订单
    start_date = datetime(year, month, 1).date()
    if month == 12:
        end_date = datetime(year + 1, 1, 1).date()
    else:
        end_date = datetime(year, month + 1, 1).date()

    orders = Order.objects.filter(
        event_date__gte=start_date,
        event_date__lt=end_date,
        status__in=['confirmed', 'delivered']
    ).select_related('created_by')

    # 构建事件数据
    events = []
    for order in orders:
        color = '#4CAF50' if order.status == 'confirmed' else '#2196F3'
        events.append({
            'id': order.id,
            'order_no': order.order_no,
            'title': f'{order.customer_name} - {order.order_no}',
            'start': order.event_date.strftime('%Y-%m-%d'),
            'color': color,
            'status': order.status
        })

    context = {
        'year': year,
        'month': month,
        'events': json.dumps(events),
    }
    return render(request, 'calendar.html', context)


@login_required
@require_permission('transfers', 'view')
def transfers_list(request):
    """转寄中心"""
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

    candidates_page = Paginator(candidates, 5).get_page(request.GET.get('candidate_page'))
    tasks_page = Paginator(tasks, 10).get_page(request.GET.get('task_page'))

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
    }
    return render(request, 'transfers.html', context)


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
        success += 1

    if success:
        messages.success(request, f'重新推荐完成：成功 {success} 条（仅更新挂靠，不生成任务）')
    if skipped_pending_task:
        messages.warning(request, f'跳过 {skipped_pending_task} 条：存在未取消转寄任务，不可重推')
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
        messages.warning(request, f'跳过 {skipped_exists} 条：已存在未取消转寄任务')
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
            messages.error(request, '您没有执行此操作的权限（transfer.complete_task）')
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

                # 1) 新单：写入发货单号并推进至已发货
                target_order.ship_tracking = tracking_no
                if target_order.status != 'delivered':
                    target_order.status = 'delivered'
                target_order.save(update_fields=['ship_tracking', 'status', 'updated_at'])

                # 对应目标单转寄锁标记为已消耗
                TransferAllocation.objects.filter(
                    target_order=target_order,
                    sku_id=transfer.sku_id,
                    status='locked'
                ).update(status='consumed')

                # 2) 来源单：写入回收单号并推进至已完成（归还 -> 完成）
                source_order.return_tracking = tracking_no
                if source_order.status in ['delivered', 'in_use']:
                    source_order.status = 'returned'
                    source_order.save(update_fields=['return_tracking', 'status', 'updated_at'])
                    source_order.status = 'completed'
                    source_order.save(update_fields=['status', 'updated_at'])
                elif source_order.status == 'returned':
                    source_order.save(update_fields=['return_tracking', 'updated_at'])
                    source_order.status = 'completed'
                    source_order.save(update_fields=['status', 'updated_at'])
                elif source_order.status == 'completed':
                    source_order.save(update_fields=['return_tracking', 'updated_at'])
                else:
                    raise ValueError(f'来源单状态为 {source_order.get_status_display()}，无法执行归还完成')

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
                    extra={'tracking_no': tracking_no},
                )
            messages.success(request, '转寄任务已完成')
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('transfers_list')


@login_required
@require_permission('transfers', 'update')
def transfer_cancel(request, transfer_id):
    """取消转寄任务"""
    if request.method == 'POST':
        if not has_action_permission(request.user, 'transfer.cancel_task'):
            messages.error(request, '您没有执行此操作的权限（transfer.cancel_task）')
            return redirect('transfers_list')
        try:
            transfer = get_object_or_404(Transfer, id=transfer_id)
            if transfer.status != 'pending':
                raise ValueError('仅待执行任务可取消')
            before_transfer = _snapshot_transfer_audit(transfer)
            transfer.status = 'cancelled'
            transfer.notes = (transfer.notes + '\n' if transfer.notes else '') + '手动取消'
            transfer.save(update_fields=['status', 'notes', 'updated_at'])
            AuditService.log_with_diff(
                user=request.user,
                action='status_change',
                module='转寄',
                target=f'任务#{transfer.id}',
                summary='取消转寄任务',
                before=before_transfer,
                after=_snapshot_transfer_audit(transfer),
            )
            messages.success(request, '转寄任务已取消')
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
    exception_nodes = 0
    for unit_id, mv in latest_by_unit.items():
        if mv.event_type in ['TRANSFER_PENDING', 'TRANSFER_SHIPPED']:
            transfer_in_transit += 1
        if mv.status in ['warning', 'timeout'] or mv.event_type == 'EXCEPTION':
            exception_nodes += 1
    today_out = UnitMovement.objects.filter(event_type='WAREHOUSE_OUT', event_time__date=today).count()
    today_returned = UnitMovement.objects.filter(event_type='RETURNED_WAREHOUSE', event_time__date=today).count()

    summary = {
        'outbound_total': outbound_total,
        'transfer_in_transit': transfer_in_transit,
        'today_out': today_out,
        'today_returned': today_returned,
        'exception_nodes': exception_nodes,
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
        if warn_reason:
            warn_reason_by_unit[unit_id] = warn_reason

    if anomaly_only:
        units = units.filter(id__in=list(warn_reason_by_unit.keys()))

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
        enriched.append((unit, latest_mv, hop_count, outbound_days, warn_reason, severity))

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

    units_page = Paginator(enriched, 10).get_page(request.GET.get('page'))
    page_units = []
    for unit, latest_mv, hop_count, outbound_days, warn_reason, severity in units_page.object_list:
        unit.latest_movement = latest_mv
        unit.hop_count = hop_count
        unit.outbound_days = outbound_days
        unit.warn_reason = warn_reason
        unit.warn_severity = severity
        page_units.append(unit)
    units_page.object_list = page_units
    for unit in units_page.object_list:
        unit.latest_movement = latest_by_unit.get(unit.id)
        unit.hop_count = hop_counts.get(unit.id, getattr(unit, 'hop_count', 0))
        unit.outbound_days = getattr(unit, 'outbound_days', (now - unit.latest_movement.event_time).days if (unit.latest_movement and unit.status != 'in_warehouse') else 0)
        unit.warn_reason = getattr(unit, 'warn_reason', warn_reason_by_unit.get(unit.id, ''))

    # 节点时间线
    timeline_qs = UnitMovement.objects.select_related(
        'unit__sku', 'from_order', 'to_order', 'transfer'
    ).order_by('-event_time')
    if event_type:
        timeline_qs = timeline_qs.filter(event_type=event_type)
    if sku_id.isdigit():
        timeline_qs = timeline_qs.filter(unit__sku_id=int(sku_id))
    timeline_page = Paginator(timeline_qs, 10).get_page(request.GET.get('timeline_page'))

    # 拓扑文本视图（按单套串联节点）
    topology_rows = []
    topology_units_qs = InventoryUnit.objects.select_related('sku').filter(is_active=True).order_by('sku__code', 'unit_no')
    if topology_sku_id.isdigit():
        topology_units_qs = topology_units_qs.filter(sku_id=int(topology_sku_id))
    if topology_unit_no:
        topology_units_qs = topology_units_qs.filter(unit_no__icontains=topology_unit_no)
    if anomaly_only:
        topology_units_qs = topology_units_qs.filter(id__in=list(warn_reason_by_unit.keys()))
    topology_units_page = Paginator(topology_units_qs, 5).get_page(request.GET.get('topology_page'))
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
    }
    return render(request, 'outbound_inventory.html', context)


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
        '在途天数', '转寄节点数', '预警', '拓扑链路'
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

        if anomaly_only and not warn_reason:
            continue

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
            warn_reason,
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
    skus = SKU.objects.filter(is_active=True).order_by('code')
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

    skus_page = Paginator(skus, 10).get_page(request.GET.get('page'))
    context = {
        'skus': skus_page,
        'skus_page': skus_page,
        'keyword': keyword,
        'category': category,
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'skus.html', context)


@login_required
@require_permission('skus', 'create')
def sku_create(request):
    """创建产品"""
    if request.method == 'POST':
        try:
            sku = SKU.objects.create(
                code=request.POST.get('code'),
                name=request.POST.get('name'),
                category=request.POST.get('category'),
                image=request.FILES.get('image'),
                rental_price=request.POST.get('rental_price'),
                deposit=request.POST.get('deposit'),
                stock=int(request.POST.get('stock', 0)),
                description=request.POST.get('description', ''),
            )
            created_units = InventoryUnitService.ensure_units_for_sku(sku)
            messages.success(request, f'SKU {sku.code} 创建成功')
            if created_units:
                messages.info(request, f'已自动创建 {created_units} 条单套编号')
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
            sku = get_object_or_404(SKU, id=sku_id)
            old_stock = int(sku.stock or 0)
            sku.code = request.POST.get('code')
            sku.name = request.POST.get('name')
            sku.category = request.POST.get('category')
            new_image = request.FILES.get('image')
            if request.POST.get('clear_image') == '1':
                sku.image = None
            elif new_image:
                sku.image = new_image
            sku.rental_price = request.POST.get('rental_price')
            sku.deposit = request.POST.get('deposit')
            sku.stock = int(request.POST.get('stock', 0))
            sku.description = request.POST.get('description', '')
            sku.save()
            created_units = 0
            if int(sku.stock or 0) > old_stock:
                created_units = InventoryUnitService.ensure_units_for_sku(sku)
            messages.success(request, f'SKU {sku.code} 更新成功')
            if created_units:
                messages.info(request, f'库存上调已自动补齐单套编号：新增 {created_units} 条')
            return redirect('skus_list')
        except Exception as e:
            messages.error(request, f'SKU更新失败：{str(e)}')

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

    purchase_orders_page = Paginator(pos, 10).get_page(request.GET.get('page'))
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

    parts_page = Paginator(parts, 10).get_page(request.GET.get('page'))
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
    movements_page = Paginator(movements, 10).get_page(request.GET.get('page'))

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
        managed_keys = [
            'ship_lead_days',
            'return_offset_days',
            'buffer_days',
            'max_transfer_gap_days',
            'warehouse_sender_name',
            'warehouse_sender_phone',
            'warehouse_sender_address',
            'transfer_pending_timeout_hours',
            'transfer_shipped_timeout_days',
            'outbound_max_days_warn',
            'outbound_max_hops_warn',
        ]
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
    }
    return render(request, 'settings.html', context)


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

    logs_page = Paginator(filtered_logs, 10).get_page(request.GET.get('page'))

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

    users_page = Paginator(users, 10).get_page(request.GET.get('page'))
    context = {
        'users': users_page,
        'users_page': users_page,
        'role_filter': role,
        'status_filter': status,
        'keyword': keyword,
        'pagination_query': _build_querystring(request, ['page']),
    }
    return render(request, 'users.html', context)


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
            deposit_paid = Decimal(request.POST.get('deposit_paid', '0') or '0')
            auto_deliver = request.POST.get('auto_deliver') == '1'
            ship_tracking = (request.POST.get('ship_tracking', '') or '').strip()
            if auto_deliver and not has_action_permission(request.user, 'order.confirm_delivery'):
                raise ValueError('您没有执行此操作的权限（order.confirm_delivery）')
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
            order = get_object_or_404(Order, id=order_id)
            if order.status == 'delivered':
                if _is_transfer_source_order_active(order):
                    raise ValueError('该订单为转寄链路订单，请前往【转寄中心】完成操作')
                OrderService.mark_as_returned(order_id, request.POST.get('return_tracking', ''), Decimal('0.00'), request.user)
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
        if not has_action_permission(request.user, 'order.force_cancel'):
            messages.error(request, '您没有执行此操作的权限（order.force_cancel）')
            return redirect('orders_list')
        try:
            reason = request.POST.get('reason', '手动取消')
            OrderService.cancel_order(order_id, reason, request.user)
            messages.success(request, '订单已取消')
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
                'stock': sku.stock,
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
