"""
核心视图模块 - 处理所有前端页面请求（第二阶段：真实数据）
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum, F
from django.db import models
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from .models import (
    Order, OrderItem, SKU, Part, PurchaseOrder, PurchaseOrderItem, PartsMovement,
    AuditLog, User, SystemSettings, Transfer, TransferAllocation
)
from .services import OrderService, ProcurementService, PartsService
from .permissions import require_permission, filter_queryset_by_permission
from .utils import (
    get_calendar_data,
    find_transfer_candidates,
    create_transfer_task,
    build_transfer_allocation_plan,
    get_transfer_match_candidates,
    build_transfer_pool_rows,
    sync_transfer_tasks_for_target_order,
)


def _build_querystring(request, exclude_keys=None):
    params = request.GET.copy()
    for key in (exclude_keys or []):
        params.pop(key, None)
    return params.urlencode()


def _log_transfer_action(user, action, target, details):
    AuditLog.objects.create(
        user=user,
        action=action,
        module='转寄',
        target=target,
        details=details,
        ip_address=None,
    )


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
    total_stock = SKU.objects.filter(is_active=True).aggregate(total=Sum('stock'))['total'] or 0
    occupied_raw = OrderItem.objects.filter(
        order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
        sku__is_active=True
    ).aggregate(total=Sum('quantity'))['total'] or 0
    transfer_allocated = TransferAllocation.objects.filter(
        target_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
        sku__is_active=True,
        status__in=['locked', 'consumed']
    ).aggregate(total=Sum('quantity'))['total'] or 0
    occupied_stock = max(occupied_raw - transfer_allocated, 0)
    warehouse_available_stock = max(total_stock - occupied_stock, 0)
    transfer_available_count = len(find_transfer_candidates())
    total_revenue = Order.objects.filter(status='completed').aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    pending_revenue = Order.objects.exclude(status__in=['completed', 'cancelled']).aggregate(total=Sum('balance'))['total'] or Decimal('0.00')

    # 统计数据
    stats = {
        'pending_orders': Order.objects.filter(status='pending').count(),
        'delivered_orders': Order.objects.filter(status='delivered').count(),
        'warehouse_available_stock': warehouse_available_stock,
        'transfer_available_count': transfer_available_count,
        'total_orders': Order.objects.count(),
        'total_skus': SKU.objects.filter(is_active=True).count(),
        'low_stock_parts': Part.objects.filter(current_stock__lt=models.F('safety_stock')).count(),
        'total_revenue': total_revenue,
        'pending_revenue': pending_revenue,
    }

    # 最近订单
    recent_orders = Order.objects.select_related('created_by').prefetch_related(
        'items__sku',
        'transfer_allocations_target__source_order',
    ).order_by('-created_at')[:5]

    # 库存不足的部件
    low_stock_parts = Part.objects.filter(
        is_active=True,
        current_stock__lt=F('safety_stock')
    ).order_by('current_stock')[:5]

    context = {
        'stats': stats,
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
    for order in orders_page.object_list:
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
            messages.success(request, f'订单创建成功：{order.order_no}')
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
    if status_filter not in ['pending', 'completed', 'cancelled', 'all']:
        status_filter = 'pending'

    # 获取转寄任务
    tasks = Transfer.objects.select_related(
        'order_from', 'order_to', 'sku'
    ).order_by('-created_at')
    if status_filter and status_filter != 'all':
        tasks = tasks.filter(status=status_filter)
    if keyword:
        keyword_lower = keyword.lower()
        tasks = tasks.filter(
            Q(order_from__order_no__icontains=keyword) |
            Q(order_to__order_no__icontains=keyword) |
            Q(sku__name__icontains=keyword) |
            Q(order_from__customer_name__icontains=keyword) |
            Q(order_to__customer_name__icontains=keyword) |
            Q(order_from__customer_phone__icontains=keyword) |
            Q(order_to__customer_phone__icontains=keyword)
        )
        candidates = [
            c for c in candidates
            if keyword_lower in (c['order'].order_no or '').lower()
            or keyword_lower in (c['item'].sku.name or '').lower()
            or keyword_lower in (c['order'].customer_name or '').lower()
            or keyword_lower in (c['order'].customer_phone or '').lower()
            or keyword_lower in (c['current_source_text'] or '').lower()
            or keyword_lower in (c['recommended_source_text'] or '').lower()
        ]

    candidates_page = Paginator(candidates, 10).get_page(request.GET.get('candidate_page'))
    tasks_page = Paginator(tasks, 10).get_page(request.GET.get('task_page'))
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

    context = {
        'candidates': candidates_page,
        'tasks': tasks_page,
        'candidates_page': candidates_page,
        'tasks_page': tasks_page,
        'keyword': keyword,
        'status_filter': status_filter,
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

        order = Order.objects.filter(id=order_id, status__in=['pending', 'confirmed']).first()
        if not order:
            skipped_invalid += 1
            continue
        item = OrderItem.objects.filter(order=order, sku_id=sku_id).first()
        if not item:
            skipped_invalid += 1
            continue
        if Transfer.objects.filter(order_to=order, sku_id=sku_id, status='pending').exists():
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
        messages.warning(request, f'跳过 {skipped_pending_task} 条：已生成待执行转寄任务，不可重推')
    if skipped_invalid:
        messages.warning(request, f'跳过 {skipped_invalid} 条：数据无效或状态不允许')
    return redirect('transfers_list')


@login_required
@require_permission('transfers', 'create')
def transfer_generate_tasks(request):
    """批量/单条生成转寄任务（与重新推荐分离）"""
    if request.method != 'POST':
        return redirect('transfers_list')
    rows = request.POST.getlist('rows[]') or request.POST.getlist('rows')
    if not rows:
        messages.error(request, '请先选择候选项')
        return redirect('transfers_list')

    success = 0
    skipped_warehouse = 0
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

        order = Order.objects.filter(id=order_id, status__in=['pending', 'confirmed']).first()
        if not order or not OrderItem.objects.filter(order=order, sku_id=sku_id).exists():
            skipped_invalid += 1
            continue
        if Transfer.objects.filter(order_to=order, sku_id=sku_id, status='pending').exists():
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
    if skipped_exists:
        messages.warning(request, f'跳过 {skipped_exists} 条：已存在待执行转寄任务')
    if skipped_invalid:
        messages.warning(request, f'跳过 {skipped_invalid} 条：数据无效或状态不允许')
    return redirect('transfers_list')


@login_required
@require_permission('transfers', 'create')
def transfer_create(request):
    """创建转寄任务"""
    if request.method == 'POST':
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
        try:
            transfer = get_object_or_404(Transfer, id=transfer_id)
            if transfer.status != 'pending':
                raise ValueError('仅待执行任务可完成')
            transfer.status = 'completed'
            transfer.save(update_fields=['status', 'updated_at'])
            AuditLog.objects.create(
                user=request.user,
                action='status_change',
                module='转寄',
                target=f'任务#{transfer.id}',
                details='标记转寄任务完成',
                ip_address=None
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
        try:
            transfer = get_object_or_404(Transfer, id=transfer_id)
            if transfer.status != 'pending':
                raise ValueError('仅待执行任务可取消')
            transfer.status = 'cancelled'
            transfer.notes = (transfer.notes + '\n' if transfer.notes else '') + '手动取消'
            transfer.save(update_fields=['status', 'notes', 'updated_at'])
            AuditLog.objects.create(
                user=request.user,
                action='status_change',
                module='转寄',
                target=f'任务#{transfer.id}',
                details='取消转寄任务',
                ip_address=None
            )
            messages.success(request, '转寄任务已取消')
        except Exception as e:
            messages.error(request, f'操作失败：{str(e)}')
    return redirect('transfers_list')


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
                rental_price=request.POST.get('rental_price'),
                deposit=request.POST.get('deposit'),
                stock=int(request.POST.get('stock', 0)),
                description=request.POST.get('description', ''),
            )
            messages.success(request, f'SKU {sku.code} 创建成功')
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
            sku.code = request.POST.get('code')
            sku.name = request.POST.get('name')
            sku.category = request.POST.get('category')
            sku.rental_price = request.POST.get('rental_price')
            sku.deposit = request.POST.get('deposit')
            sku.stock = int(request.POST.get('stock', 0))
            sku.description = request.POST.get('description', '')
            sku.save()
            messages.success(request, f'SKU {sku.code} 更新成功')
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
    if category:
        parts = parts.filter(category=category)
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
    if request.method == 'POST':
        # 更新设置
        for key in [
            'ship_lead_days',
            'return_offset_days',
            'buffer_days',
            'max_transfer_gap_days',
            'warehouse_sender_name',
            'warehouse_sender_phone',
            'warehouse_sender_address',
        ]:
            value = request.POST.get(key)
            if value:
                SystemSettings.objects.update_or_create(
                    key=key,
                    defaults={'value': value}
                )
        messages.success(request, '设置保存成功')
        return redirect('settings')

    # 获取设置
    settings = {}
    for setting in SystemSettings.objects.all():
        settings[setting.key] = setting.value

    context = {
        'settings': settings,
    }
    return render(request, 'settings.html', context)


@login_required
@require_permission('audit_logs', 'view')
def audit_logs(request):
    """操作日志"""
    logs = AuditLog.objects.select_related('user').order_by('-created_at')
    action = (request.GET.get('action', '') or '').strip()
    keyword = (request.GET.get('keyword', '') or '').strip()
    start_date = (request.GET.get('start_date', '') or '').strip()
    end_date = (request.GET.get('end_date', '') or '').strip()
    if action:
        logs = logs.filter(action=action)
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
    logs_page = Paginator(logs, 10).get_page(request.GET.get('page'))

    context = {
        'logs': logs_page,
        'logs_page': logs_page,
        'action': action,
        'keyword': keyword,
        'start_date': start_date,
        'end_date': end_date,
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
        try:
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
