"""
订单业务逻辑服务
处理订单创建、更新、状态流转等核心业务
"""
from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from ..models import Order, OrderItem, SKU, AuditLog
from ..utils import check_sku_availability, calculate_order_dates, calculate_order_amount


class OrderService:
    """订单服务"""

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
        # 1. 验证库存
        for item in data['items']:
            result = check_sku_availability(
                sku_id=item['sku_id'],
                event_date=data['event_date'],
                quantity=item['quantity']
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
                subtotal=(sku.rental_price + sku.deposit) * item_data['quantity']
            )

        # 6. 记录日志
        AuditLog.objects.create(
            user=user,
            action='create',
            module='订单',
            target=order.order_no,
            details=f"创建订单：{order.customer_name}，活动日期：{order.event_date}",
            ip_address=None
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

        # 更新基本信息
        order.customer_name = data.get('customer_name', order.customer_name)
        order.customer_phone = data.get('customer_phone', order.customer_phone)
        order.customer_email = data.get('customer_email', order.customer_email)
        order.delivery_address = data.get('delivery_address', order.delivery_address)
        order.return_address = data.get('return_address', order.return_address)
        order.notes = data.get('notes', order.notes)

        # 如果修改了日期，重新计算
        if 'event_date' in data or 'rental_days' in data:
            event_date = data.get('event_date', order.event_date)
            rental_days = data.get('rental_days', order.rental_days)

            dates = calculate_order_dates(event_date, rental_days)
            order.event_date = event_date
            order.rental_days = rental_days
            order.ship_date = dates['ship_date']
            order.return_date = dates['return_date']

        order.save()

        # 记录日志
        AuditLog.objects.create(
            user=user,
            action='update',
            module='订单',
            target=order.order_no,
            details=f"修改订单信息",
            ip_address=None
        )

        return order

    @staticmethod
    @transaction.atomic
    def confirm_order(order_id, deposit_paid, user):
        """
        确认订单（收取押金）

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

        order.status = 'confirmed'
        order.deposit_paid = Decimal(str(deposit_paid))
        order.balance = order.total_amount - order.deposit_paid
        order.save()

        # 记录日志
        AuditLog.objects.create(
            user=user,
            action='status_change',
            module='订单',
            target=order.order_no,
            details=f"确认订单，收取押金 ¥{deposit_paid}",
            ip_address=None
        )

        return order

    @staticmethod
    @transaction.atomic
    def mark_as_delivered(order_id, ship_tracking, user):
        """
        标记已送达

        Args:
            order_id: 订单ID
            ship_tracking: 发货单号
            user: 操作人

        Returns:
            Order: 订单对象
        """
        order = Order.objects.get(id=order_id)

        if order.status != 'confirmed':
            raise ValueError(f"订单状态为 {order.get_status_display()}，无法标记送达")

        order.status = 'delivered'
        order.ship_tracking = ship_tracking
        order.save()

        # 记录日志
        AuditLog.objects.create(
            user=user,
            action='status_change',
            module='订单',
            target=order.order_no,
            details=f"标记已送达，发货单号：{ship_tracking}",
            ip_address=None
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

        order.status = 'returned'
        order.return_tracking = return_tracking

        # 更新尾款
        if balance_paid:
            order.balance = order.balance - Decimal(str(balance_paid))

        order.save()

        # 记录日志
        AuditLog.objects.create(
            user=user,
            action='status_change',
            module='订单',
            target=order.order_no,
            details=f"标记已归还，回收单号：{return_tracking}",
            ip_address=None
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

        order.status = 'completed'
        order.save()

        # 记录日志
        AuditLog.objects.create(
            user=user,
            action='status_change',
            module='订单',
            target=order.order_no,
            details=f"完成订单，退还押金 ¥{order.deposit_paid}",
            ip_address=None
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

        order.status = 'cancelled'
        order.notes = f"{order.notes}\n取消原因：{reason}" if order.notes else f"取消原因：{reason}"
        order.save()

        # 记录日志
        AuditLog.objects.create(
            user=user,
            action='status_change',
            module='订单',
            target=order.order_no,
            details=f"取消订单，原因：{reason}",
            ip_address=None
        )

        return order
