"""
核心视图模块 - 处理所有前端页面请求（第二阶段：真实数据）
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
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
    """工作台 - 订单处理中心"""
    # 根据权限过滤订单
    orders = filter_queryset_by_permission(
        Order.objects.select_related('created_by').prefetch_related(
            'items__sku',
            'transfer_allocations_target__source_order',
        ),
        request.user,
        'Order'
    )

    # 按状态分组
    pending_orders = orders.filter(status__in=['pending', 'confirmed']).order_by('-created_at')
    delivered_orders = orders.filter(status='delivered').order_by('return_date')
    returned_orders = orders.filter(status='returned').order_by('return_date')

    context = {
        'pending_orders': pending_orders,
        'delivered_orders': delivered_orders,
        'returned_orders': returned_orders,
    }
    return render(request, 'workbench.html', context)


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

    context = {
        'orders': orders,
        'status_filter': status_filter,
        'keyword': keyword,
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
                    items.append({
                        'sku_id': int(sku_id),
                        'quantity': int(quantity) if quantity else 1,
                        'transfer_source_order_id': int(source_order_id) if source_order_id else None,
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
            data = {
                'customer_name': request.POST.get('customer_name'),
                'customer_phone': request.POST.get('customer_phone'),
                'customer_email': request.POST.get('customer_email', ''),
                'delivery_address': request.POST.get('delivery_address'),
                'return_address': request.POST.get('return_address', ''),
                'notes': request.POST.get('notes', ''),
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

    context = {
        'order': order,
        'skus': skus,
        'mode': 'edit',
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
    # 获取转寄候选
    candidates = find_transfer_candidates()

    # 获取转寄任务
    tasks = Transfer.objects.select_related(
        'order_from', 'order_to', 'sku'
    ).order_by('-created_at')

    context = {
        'candidates': candidates,
        'tasks': tasks,
    }
    return render(request, 'transfers.html', context)


@login_required
@require_permission('transfers', 'create')
def transfer_create(request):
    """创建转寄任务"""
    if request.method == 'POST':
        try:
            order_from_id = int(request.POST.get('order_from_id'))
            order_to_id = int(request.POST.get('order_to_id'))
            sku_id = int(request.POST.get('sku_id'))
            create_transfer_task(order_from_id, order_to_id, sku_id, request.user)
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

    context = {
        'skus': skus,
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
@require_permission('procurement', 'view')
def purchase_orders_list(request):
    """采购订单列表"""
    pos = PurchaseOrder.objects.select_related('created_by').prefetch_related('items').order_by('-created_at')

    # 筛选
    status_filter = request.GET.get('status', '')
    if status_filter:
        pos = pos.filter(status=status_filter)

    context = {
        'purchase_orders': pos,
        'status_filter': status_filter,
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
    if category:
        parts = parts.filter(category=category)

    context = {
        'parts': parts,
        'category': category,
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
    movements = PartsMovement.objects.select_related('part', 'operator').order_by('-created_at')[:100]

    context = {
        'movements': movements,
    }
    return render(request, 'procurement/parts_movements.html', context)


@login_required
@require_permission('settings', 'view')
def settings_view(request):
    """系统设置"""
    if request.method == 'POST':
        # 更新设置
        for key in ['ship_lead_days', 'return_offset_days', 'buffer_days', 'max_transfer_gap_days']:
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
    logs = AuditLog.objects.select_related('user').order_by('-created_at')[:100]

    context = {
        'logs': logs,
    }
    return render(request, 'audit_logs.html', context)


@login_required
@require_permission('users', 'view')
def users_list(request):
    """用户管理"""
    users = User.objects.all().order_by('-created_at')

    context = {
        'users': users,
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
        preferred_source_order_id = int(preferred_source_order_id_raw) if preferred_source_order_id_raw else None

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
                'distance_score': str(c['distance_score']),
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
