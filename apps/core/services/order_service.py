"""
订单业务逻辑服务
处理订单创建、更新、状态流转等核心业务
"""
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from decimal import Decimal
from ..models import Order, OrderItem, SKU, TransferAllocation
from .inventory_unit_service import InventoryUnitService
from .audit_service import AuditService
from ..utils import (
    check_sku_availability,
    calculate_order_dates,
    calculate_order_amount,
    build_transfer_allocation_plan,
    sync_transfer_tasks_for_target_order,
)


class OrderService:
    """订单服务"""

    @staticmethod
    def _snapshot_order(order):
        items = list(
            order.items.select_related('sku')
            .order_by('id')
            .values(
                'sku_id',
                'sku__code',
                'sku__name',
                'quantity',
                'rental_price',
                'deposit',
                'subtotal',
            )
        )
        allocations = list(
            order.transfer_allocations_target.select_related('source_order', 'sku')
            .filter(status__in=['locked', 'consumed'])
            .order_by('id')
            .values(
                'source_order_id',
                'source_order__order_no',
                'sku_id',
                'sku__code',
                'quantity',
                'status',
            )
        )
        return {
            'id': order.id,
            'order_no': order.order_no,
            'status': order.status,
            'customer_name': order.customer_name,
            'customer_phone': order.customer_phone,
            'delivery_address': order.delivery_address,
            'event_date': order.event_date,
            'rental_days': order.rental_days,
            'total_amount': order.total_amount,
            'deposit_paid': order.deposit_paid,
            'balance': order.balance,
            'notes': order.notes,
            'ship_tracking': order.ship_tracking,
            'return_tracking': order.return_tracking,
            'items': items,
            'allocations': allocations,
        }

    @staticmethod
    @transaction.atomic
    def create_order(data, user):
        """
        创建订单

        Args:
            data: 订单数据 {
                'customer_name': str,
                'customer_phone': str,
                'customer_email': str,
                'delivery_address': str,
                'return_address': str,
                'event_date': date,
                'rental_days': int,
                'notes': str,
                'items': [
                    {'sku_id': int, 'quantity': int},
                    ...
                ]
            }
            user: 创建人

        Returns:
            Order: 订单对象

        Raises:
            ValueError: 库存不足或数据验证失败
        """
        # 1. 先构建转寄分配方案，再验证仓库库存
        transfer_plans = []
        for item in data['items']:
            if item.get('force_warehouse'):
                plan = {
                    'allocations': [],
                    'warehouse_needed': item['quantity'],
                    'candidates': [],
                }
            else:
                plan = build_transfer_allocation_plan(
                    delivery_address=data['delivery_address'],
                    target_event_date=data['event_date'],
                    sku_id=item['sku_id'],
                    quantity=item['quantity'],
                    preferred_source_order_id=item.get('transfer_source_order_id'),
                )
            transfer_plans.append(plan)

            warehouse_needed = plan['warehouse_needed']
            if warehouse_needed > 0:
                result = check_sku_availability(
                    sku_id=item['sku_id'],
                    event_date=data['event_date'],
                    quantity=warehouse_needed,
                    rental_days=data.get('rental_days', 1)
                )
                if not result['available']:
                    sku = SKU.objects.get(id=item['sku_id'])
                    raise ValueError(f"SKU {sku.name} 库存不足：{result['message']}")

        # 2. 计算日期
        dates = calculate_order_dates(data['event_date'], data.get('rental_days', 1))

        # 3. 计算金额
        amount_info = calculate_order_amount(data['items'])

        # 4. 创建订单
        order = Order.objects.create(
            customer_name=data['customer_name'],
            customer_phone=data['customer_phone'],
            customer_email=data.get('customer_email', ''),
            delivery_address=data['delivery_address'],
            return_address=data.get('return_address', data['delivery_address']),
            event_date=data['event_date'],
            rental_days=data.get('rental_days', 1),
            ship_date=dates['ship_date'],
            return_date=dates['return_date'],
            total_amount=amount_info['total_amount'],
            deposit_paid=Decimal('0.00'),
            balance=amount_info['total_amount'],
            status='pending',
            notes=data.get('notes', ''),
            created_by=user
        )

        # 5. 创建订单明细
        for item_data in data['items']:
            sku = SKU.objects.get(id=item_data['sku_id'])
            OrderItem.objects.create(
                order=order,
                sku=sku,
                quantity=item_data['quantity'],
                rental_price=sku.rental_price,
                deposit=sku.deposit,
                # 小计仅统计租金，押金单独记录
                subtotal=sku.rental_price * item_data['quantity']
            )

        # 5.1 创建转寄分配锁（创建即锁定）
        for index, item_data in enumerate(data['items']):
            plan = transfer_plans[index] if index < len(transfer_plans) else {}
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
                    created_by=user,
                )
        sync_transfer_tasks_for_target_order(order, user)

        # 6. 记录日志
        AuditService.log_with_diff(
            user=user,
            action='create',
            module='订单',
            target=order.order_no,
            summary='创建订单',
            before={},
            after=OrderService._snapshot_order(order),
            extra={
                'customer_name': order.customer_name,
                'event_date': order.event_date,
            },
        )

        return order

    @staticmethod
    @transaction.atomic
    def update_order(order_id, data, user):
        """
        更新订单

        Args:
            order_id: 订单ID
            data: 更新数据
            user: 操作人

        Returns:
            Order: 订单对象
        """
        order = Order.objects.get(id=order_id)

        # 只有待处理和已确认的订单可以编辑
        if order.status not in ['pending', 'confirmed']:
            raise ValueError(f"订单状态为 {order.get_status_display()}，无法编辑")
        before_snapshot = OrderService._snapshot_order(order)

        items = data.get('items')
        if not items:
            raise ValueError('请至少保留一条订单明细')

        # 1) 先基于新数据做转寄/仓库复核
        event_date = data.get('event_date', order.event_date)
        rental_days = data.get('rental_days', order.rental_days)
        delivery_address = data.get('delivery_address', order.delivery_address)
        transfer_plans = []
        for item in items:
            if item.get('force_warehouse'):
                plan = {
                    'allocations': [],
                    'warehouse_needed': item['quantity'],
                    'candidates': [],
                }
            else:
                plan = build_transfer_allocation_plan(
                    delivery_address=delivery_address,
                    target_event_date=event_date,
                    sku_id=item['sku_id'],
                    quantity=item['quantity'],
                    preferred_source_order_id=item.get('transfer_source_order_id'),
                    exclude_target_order_id=order.id,
                )
            transfer_plans.append(plan)
            warehouse_needed = plan['warehouse_needed']
            if warehouse_needed > 0:
                result = check_sku_availability(
                    sku_id=item['sku_id'],
                    event_date=event_date,
                    quantity=warehouse_needed,
                    exclude_order_id=order.id,
                    rental_days=rental_days
                )
                if not result['available']:
                    sku = SKU.objects.get(id=item['sku_id'])
                    raise ValueError(f"SKU {sku.name} 库存不足：{result['message']}")

        # 2) 计算日期/金额
        dates = calculate_order_dates(event_date, rental_days)
        amount_info = calculate_order_amount(items)

        # 更新基本信息
        order.customer_name = data.get('customer_name', order.customer_name)
        order.customer_phone = data.get('customer_phone', order.customer_phone)
        order.customer_email = data.get('customer_email', order.customer_email)
        order.delivery_address = delivery_address
        order.return_address = data.get('return_address', order.return_address)
        order.notes = data.get('notes', order.notes)
        order.event_date = event_date
        order.rental_days = rental_days
        order.ship_date = dates['ship_date']
        order.return_date = dates['return_date']
        order.total_amount = amount_info['total_amount']
        # 押金不冲抵租金尾款，编辑后按新租金重置尾款
        order.balance = amount_info['total_amount']

        order.save()

        # 3) 明细重建
        order.items.all().delete()
        for item_data in items:
            sku = SKU.objects.get(id=item_data['sku_id'])
            OrderItem.objects.create(
                order=order,
                sku=sku,
                quantity=item_data['quantity'],
                rental_price=sku.rental_price,
                deposit=sku.deposit,
                subtotal=sku.rental_price * item_data['quantity']
            )

        # 4) 释放旧转寄锁并按新方案重建
        TransferAllocation.objects.filter(
            target_order=order,
            status='locked'
        ).update(status='released')
        for plan in transfer_plans:
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
                    created_by=user,
                )
        sync_transfer_tasks_for_target_order(order, user)

        # 记录日志
        AuditService.log_with_diff(
            user=user,
            action='update',
            module='订单',
            target=order.order_no,
            summary='修改订单信息',
            before=before_snapshot,
            after=OrderService._snapshot_order(order),
        )

        return order

    @staticmethod
    @transaction.atomic
    def confirm_order(order_id, deposit_paid, user):
        """
        确认订单（收取押金并进入待发货）

        Args:
            order_id: 订单ID
            deposit_paid: 已付押金
            user: 操作人

        Returns:
            Order: 订单对象
        """
        order = Order.objects.get(id=order_id)

        if order.status != 'pending':
            raise ValueError(f"订单状态为 {order.get_status_display()}，无法确认")
        before_snapshot = OrderService._snapshot_order(order)

        order.status = 'confirmed'
        order.deposit_paid = Decimal(str(deposit_paid))
        # 押金不冲抵租金尾款
        order.balance = order.total_amount
        order.save()

        # 记录日志
        AuditService.log_with_diff(
            user=user,
            action='status_change',
            module='订单',
            target=order.order_no,
            summary='确认订单',
            before=before_snapshot,
            after=OrderService._snapshot_order(order),
            extra={'deposit_paid_input': str(deposit_paid)},
        )

        return order

    @staticmethod
    @transaction.atomic
    def mark_as_delivered(order_id, ship_tracking, user):
        """
        标记已发货

        Args:
            order_id: 订单ID
            ship_tracking: 发货单号
            user: 操作人

        Returns:
            Order: 订单对象
        """
        order = Order.objects.get(id=order_id)

        if order.status != 'confirmed':
            raise ValueError(f"订单状态为 {order.get_status_display()}，无法标记发货")
        before_snapshot = OrderService._snapshot_order(order)

        order.status = 'delivered'
        order.ship_tracking = ship_tracking
        order.save()

        # 仓库发货单套分配（转寄部分不占用仓库单套）
        item_map = {item.sku_id: item for item in order.items.select_related('sku').all()}
        transfer_qty_map = {
            row['sku_id']: int(row['qty'] or 0)
            for row in TransferAllocation.objects.filter(
                target_order=order,
                status='locked'
            ).values('sku_id').annotate(qty=Sum('quantity'))
        }
        for sku_id, item in item_map.items():
            transfer_qty = transfer_qty_map.get(sku_id, 0)
            warehouse_qty = max(int(item.quantity or 0) - transfer_qty, 0)
            if warehouse_qty <= 0:
                continue
            InventoryUnitService.allocate_from_warehouse(
                order=order,
                sku=item.sku,
                quantity=warehouse_qty,
                tracking_no=ship_tracking or '',
                operator=user,
            )

        # 目标订单发货后，转寄锁进入已消耗状态
        TransferAllocation.objects.filter(
            target_order=order,
            status='locked'
        ).update(status='consumed')

        # 记录日志
        AuditService.log_with_diff(
            user=user,
            action='status_change',
            module='订单',
            target=order.order_no,
            summary='标记已发货',
            before=before_snapshot,
            after=OrderService._snapshot_order(order),
            extra={'ship_tracking': ship_tracking},
        )

        return order

    @staticmethod
    @transaction.atomic
    def mark_as_returned(order_id, return_tracking, balance_paid, user):
        """
        标记已归还

        Args:
            order_id: 订单ID
            return_tracking: 回收单号
            balance_paid: 已付尾款
            user: 操作人

        Returns:
            Order: 订单对象
        """
        order = Order.objects.get(id=order_id)

        if order.status not in ['delivered', 'in_use']:
            raise ValueError(f"订单状态为 {order.get_status_display()}，无法标记归还")
        before_snapshot = OrderService._snapshot_order(order)

        order.status = 'returned'
        order.return_tracking = return_tracking

        # 更新尾款
        if balance_paid:
            order.balance = order.balance - Decimal(str(balance_paid))

        order.save()
        InventoryUnitService.return_to_warehouse(order, return_tracking or '', operator=user)

        # 记录日志
        AuditService.log_with_diff(
            user=user,
            action='status_change',
            module='订单',
            target=order.order_no,
            summary='标记已归还',
            before=before_snapshot,
            after=OrderService._snapshot_order(order),
            extra={
                'return_tracking': return_tracking,
                'balance_paid_input': str(balance_paid or 0),
            },
        )

        return order

    @staticmethod
    @transaction.atomic
    def complete_order(order_id, user):
        """
        完成订单（退还押金）

        Args:
            order_id: 订单ID
            user: 操作人

        Returns:
            Order: 订单对象
        """
        order = Order.objects.get(id=order_id)

        if order.status != 'returned':
            raise ValueError(f"订单状态为 {order.get_status_display()}，无法完成")
        before_snapshot = OrderService._snapshot_order(order)

        order.status = 'completed'
        order.save()

        # 记录日志
        AuditService.log_with_diff(
            user=user,
            action='status_change',
            module='订单',
            target=order.order_no,
            summary='完成订单',
            before=before_snapshot,
            after=OrderService._snapshot_order(order),
            extra={'deposit_paid': str(order.deposit_paid)},
        )

        return order

    @staticmethod
    @transaction.atomic
    def cancel_order(order_id, reason, user):
        """
        取消订单

        Args:
            order_id: 订单ID
            reason: 取消原因
            user: 操作人

        Returns:
            Order: 订单对象
        """
        order = Order.objects.get(id=order_id)

        if order.status in ['completed', 'cancelled']:
            raise ValueError(f"订单状态为 {order.get_status_display()}，无法取消")
        before_snapshot = OrderService._snapshot_order(order)

        order.status = 'cancelled'
        order.notes = f"{order.notes}\n取消原因：{reason}" if order.notes else f"取消原因：{reason}"
        order.save()

        # 释放该目标订单占用的转寄锁
        TransferAllocation.objects.filter(
            target_order=order,
            status='locked'
        ).update(status='released')

        # 记录日志
        AuditService.log_with_diff(
            user=user,
            action='status_change',
            module='订单',
            target=order.order_no,
            summary='取消订单',
            before=before_snapshot,
            after=OrderService._snapshot_order(order),
            extra={'reason': reason},
        )

        return order

