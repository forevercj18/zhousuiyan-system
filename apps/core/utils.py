"""
业务工具函数
包含：系统设置获取、库存校验、排期计算、转寄匹配等
"""
from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum, Q
from .models import SystemSettings, Order, OrderItem, SKU, Transfer


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


def check_sku_availability(sku_id, event_date, quantity=1, exclude_order_id=None):
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

    # 获取系统设置
    settings = get_system_settings()
    ship_lead_days = settings['ship_lead_days']
    return_offset_days = settings['return_offset_days']
    buffer_days = settings['buffer_days']

    # 计算占用期间（发货日到回收日 + 缓冲天数）
    occupy_start = event_date - timedelta(days=ship_lead_days)
    occupy_end = event_date + timedelta(days=return_offset_days + buffer_days)

    # 查询该期间内有冲突的订单
    query = Q(
        status__in=['confirmed', 'delivered', 'in_use'],
        event_date__gte=occupy_start,
        event_date__lte=occupy_end
    )

    if exclude_order_id:
        query &= ~Q(id=exclude_order_id)

    # 统计已占用数量
    occupied = OrderItem.objects.filter(
        order__in=Order.objects.filter(query),
        sku_id=sku_id
    ).aggregate(total=Sum('quantity'))['total'] or 0

    available_count = sku.stock - occupied

    return {
        'available': available_count >= quantity,
        'current_stock': sku.stock,
        'occupied': occupied,
        'available_count': available_count,
        'message': f'可用数量：{available_count}/{sku.stock}' if available_count >= quantity else f'库存不足，仅剩{available_count}套'
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
                status__in=['confirmed', 'delivered', 'in_use'],
                event_date=d,
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

    total_amount = total_deposit + total_rental

    return {
        'total_amount': total_amount,
        'total_deposit': total_deposit,
        'total_rental': total_rental
    }
