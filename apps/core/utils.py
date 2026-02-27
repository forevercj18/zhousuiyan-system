"""
业务工具函数
包含：系统设置获取、库存校验、排期计算、转寄匹配等
"""
from datetime import timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from django.db.models import Sum, Q
from .models import SystemSettings, Order, OrderItem, SKU, Transfer, TransferAllocation


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
    settings.setdefault('buffer_days', 1)
    settings.setdefault('max_transfer_gap_days', 3)

    return settings


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
        sku_id=sku_id,
        status__in=['locked', 'consumed']
    ).aggregate(total=Sum('quantity'))['total'] or 0
    occupied = max(occupied_raw - transfer_allocated, 0)

    raw_available_count = sku.stock - occupied
    available_count = max(raw_available_count, 0)
    overbooked_count = max(-raw_available_count, 0)

    return {
        'available': raw_available_count >= quantity,
        'current_stock': sku.stock,
        'occupied': occupied,
        'available_count': available_count,
        'overbooked_count': overbooked_count,
        'message': (
            f'仓库可用：{available_count}/{sku.stock}（占用：{occupied}）'
            if raw_available_count >= quantity
            else (
                f'仓库库存不足，仅剩{available_count}套（占用：{occupied}）'
                if overbooked_count == 0
                else f'仓库库存不足，当前超占{overbooked_count}套（占用：{occupied}）'
            )
        )
    }


def _address_distance_score(source_address, target_address):
    """地址相似度转换为距离分值，数值越小越近。"""
    left = (source_address or '').strip().lower()
    right = (target_address or '').strip().lower()
    if not left or not right:
        return Decimal('999.0000')
    ratio = SequenceMatcher(None, left, right).ratio()
    return Decimal(str(round((1 - ratio) * 100, 4)))


def get_transfer_match_candidates(delivery_address, target_event_date, sku_id):
    """
    获取创建订单时的转寄候选（不分配数量）
    规则：
    1) 来源订单已发货；2) 同SKU；3) 来源预定日期至少早6天；
    4) 排序：来源预定日期 ASC -> 地址距离分值 ASC -> 来源单号 ASC
    """
    min_source_date = target_event_date - timedelta(days=6)
    lock_start = target_event_date - timedelta(days=5)
    lock_end = target_event_date + timedelta(days=5)

    source_orders = Order.objects.filter(
        status='delivered',
        event_date__lte=min_source_date,
        items__sku_id=sku_id
    ).distinct().prefetch_related('items__sku')

    candidates = []
    for order in source_orders:
        source_qty = OrderItem.objects.filter(order=order, sku_id=sku_id).aggregate(total=Sum('quantity'))['total'] or 0
        reserved_qty = TransferAllocation.objects.filter(
            source_order=order,
            sku_id=sku_id,
            status__in=['locked', 'consumed'],
            target_event_date__gte=lock_start,
            target_event_date__lte=lock_end
        ).aggregate(total=Sum('quantity'))['total'] or 0
        available_qty = max(source_qty - reserved_qty, 0)
        if available_qty <= 0:
            continue

        distance_score = _address_distance_score(order.delivery_address, delivery_address)
        candidates.append({
            'source_order': order,
            'available_qty': available_qty,
            'distance_score': distance_score,
            'lock_window_start': lock_start,
            'lock_window_end': lock_end,
        })

    candidates.sort(key=lambda item: (item['source_order'].event_date, item['distance_score'], item['source_order'].order_no))
    return candidates


def build_transfer_allocation_plan(delivery_address, target_event_date, sku_id, quantity, preferred_source_order_id=None):
    """根据候选生成分配方案：优先转寄，不足部分走仓库。"""
    candidates = get_transfer_match_candidates(delivery_address, target_event_date, sku_id)
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
    settings = get_system_settings()
    max_gap = settings['max_transfer_gap_days']

    candidates = []

    # 查找已送达待回收的订单
    orders_from = Order.objects.filter(
        status='delivered'
    ).prefetch_related('items__sku')

    # 查找已确认待发货的订单
    orders_to = Order.objects.filter(
        status='confirmed'
    ).prefetch_related('items__sku')

    for order_from in orders_from:
        for item_from in order_from.items.all():
            # 查找相同SKU的待发货订单
            for order_to in orders_to:
                for item_to in order_to.items.all():
                    if item_from.sku_id == item_to.sku_id:
                        # 计算间隔天数
                        gap = (order_to.ship_date - order_from.return_date).days

                        # 如果间隔在允许范围内
                        if 0 <= gap <= max_gap:
                            # 计算节省成本（假设往返运费各50元）
                            cost_saved = Decimal('100.00')

                            candidates.append({
                                'order_from': order_from,
                                'order_to': order_to,
                                'sku': item_from.sku,
                                'gap_days': gap,
                                'cost_saved': cost_saved
                            })

    # 按间隔天数排序（间隔越小越优先）
    candidates.sort(key=lambda x: x['gap_days'])

    return candidates


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

    gap_days = (order_to.ship_date - order_from.return_date).days
    cost_saved = Decimal('100.00')  # 假设节省100元运费

    transfer = Transfer.objects.create(
        order_from=order_from,
        order_to=order_to,
        sku=sku,
        quantity=1,
        gap_days=gap_days,
        cost_saved=cost_saved,
        status='pending',
        created_by=user
    )

    return transfer


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

            available = sku.stock - occupied

            # 判断状态
            if available == 0:
                status = 'full'
            elif available <= sku.stock * 0.2:
                status = 'tight'
            else:
                status = 'ok'

            data[sku.id][d] = {
                'occupied': occupied,
                'available': available,
                'total': sku.stock,
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
