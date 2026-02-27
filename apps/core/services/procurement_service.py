"""
采购和部件库存业务逻辑服务
"""
from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from ..models import PurchaseOrder, PurchaseOrderItem, Part, PartsMovement, AuditLog


class ProcurementService:
    """采购服务"""

    @staticmethod
    @transaction.atomic
    def create_purchase_order(data, user):
        """
        创建采购单

        Args:
            data: 采购单数据 {
                'channel': str,
                'supplier': str,
                'link': str,
                'order_date': date,
                'arrival_date': date,
                'notes': str,
                'items': [
                    {
                        'part_id': int (可选),
                        'part_name': str,
                        'spec': str,
                        'unit': str,
                        'quantity': int,
                        'unit_price': Decimal
                    },
                    ...
                ]
            }
            user: 创建人

        Returns:
            PurchaseOrder: 采购单对象
        """
        # 1. 创建采购单
        po = PurchaseOrder.objects.create(
            channel=data['channel'],
            supplier=data['supplier'],
            link=data.get('link', ''),
            order_date=data['order_date'],
            arrival_date=data.get('arrival_date'),
            status='draft',
            notes=data.get('notes', ''),
            created_by=user
        )

        # 2. 创建明细
        total_amount = Decimal('0.00')
        for item_data in data['items']:
            part = None
            if 'part_id' in item_data and item_data['part_id']:
                part = Part.objects.get(id=item_data['part_id'])

            item = PurchaseOrderItem.objects.create(
                purchase_order=po,
                part=part,
                part_name=item_data['part_name'],
                spec=item_data.get('spec', ''),
                unit=item_data.get('unit', '个'),
                quantity=item_data['quantity'],
                unit_price=Decimal(str(item_data['unit_price'])),
                subtotal=Decimal(str(item_data['unit_price'])) * item_data['quantity']
            )
            total_amount += item.subtotal

        # 3. 更新总金额
        po.total_amount = total_amount
        po.save()

        # 4. 记录日志
        AuditLog.objects.create(
            user=user,
            action='create',
            module='采购',
            target=po.po_no,
            details=f"创建采购单：{po.supplier}，总金额 ¥{total_amount}",
            ip_address=None
        )

        return po

    @staticmethod
    @transaction.atomic
    def mark_as_ordered(po_id, user):
        """标记已下单"""
        po = PurchaseOrder.objects.get(id=po_id)

        if po.status != 'draft':
            raise ValueError(f"采购单状态为 {po.get_status_display()}，无法标记已下单")

        po.status = 'ordered'
        po.save()

        AuditLog.objects.create(
            user=user,
            action='status_change',
            module='采购',
            target=po.po_no,
            details=f"标记已下单",
            ip_address=None
        )

        return po

    @staticmethod
    @transaction.atomic
    def mark_as_arrived(po_id, user):
        """标记已到货"""
        po = PurchaseOrder.objects.get(id=po_id)

        if po.status != 'ordered':
            raise ValueError(f"采购单状态为 {po.get_status_display()}，无法标记已到货")

        po.status = 'arrived'
        if not po.arrival_date:
            po.arrival_date = timezone.now().date()
        po.save()

        AuditLog.objects.create(
            user=user,
            action='status_change',
            module='采购',
            target=po.po_no,
            details=f"标记已到货",
            ip_address=None
        )

        return po

    @staticmethod
    @transaction.atomic
    def mark_as_stocked(po_id, user):
        """
        标记已入库（自动更新部件库存）
        """
        po = PurchaseOrder.objects.get(id=po_id)

        if po.status != 'arrived':
            raise ValueError(f"采购单状态为 {po.get_status_display()}，无法标记已入库")

        # 1. 更新采购单状态
        po.status = 'stocked'
        po.save()

        # 2. 处理每个明细项
        for item in po.items.all():
            # 如果关联了部件，自动入库
            if item.part:
                PartsMovement.objects.create(
                    part=item.part,
                    type='inbound',
                    quantity=item.quantity,
                    related_doc=po.po_no,
                    notes=f"采购入库：{item.part_name}",
                    operator=user
                )
            else:
                # 如果没有关联部件，尝试根据名称和规格查找或创建
                part, created = Part.objects.get_or_create(
                    name=item.part_name,
                    spec=item.spec,
                    defaults={
                        'unit': item.unit,
                        'current_stock': 0,
                        'safety_stock': 0,
                        'category': 'other'
                    }
                )

                # 创建入库流水
                PartsMovement.objects.create(
                    part=part,
                    type='inbound',
                    quantity=item.quantity,
                    related_doc=po.po_no,
                    notes=f"采购入库：{item.part_name}",
                    operator=user
                )

                # 关联部件
                item.part = part
                item.save()

        # 3. 记录日志
        AuditLog.objects.create(
            user=user,
            action='status_change',
            module='采购',
            target=po.po_no,
            details=f"标记已入库，共 {po.items.count()} 个明细项",
            ip_address=None
        )

        return po


class PartsService:
    """部件库存服务"""

    @staticmethod
    @transaction.atomic
    def inbound(part_id, quantity, related_doc, notes, user):
        """
        部件入库

        Args:
            part_id: 部件ID
            quantity: 数量
            related_doc: 关联单据
            notes: 备注
            user: 操作人

        Returns:
            PartsMovement: 流水记录
        """
        part = Part.objects.get(id=part_id)

        movement = PartsMovement.objects.create(
            part=part,
            type='inbound',
            quantity=quantity,
            related_doc=related_doc,
            notes=notes,
            operator=user
        )

        AuditLog.objects.create(
            user=user,
            action='inbound',
            module='部件',
            target=part.name,
            details=f"入库 {quantity} {part.unit}",
            ip_address=None
        )

        return movement

    @staticmethod
    @transaction.atomic
    def outbound(part_id, quantity, related_doc, notes, user):
        """
        部件出库

        Args:
            part_id: 部件ID
            quantity: 数量
            related_doc: 关联单据
            notes: 备注
            user: 操作人

        Returns:
            PartsMovement: 流水记录
        """
        part = Part.objects.get(id=part_id)

        # 检查库存是否充足
        if part.current_stock < quantity:
            raise ValueError(f"库存不足，当前库存：{part.current_stock} {part.unit}")

        movement = PartsMovement.objects.create(
            part=part,
            type='outbound',
            quantity=quantity,
            related_doc=related_doc,
            notes=notes,
            operator=user
        )

        AuditLog.objects.create(
            user=user,
            action='outbound',
            module='部件',
            target=part.name,
            details=f"出库 {quantity} {part.unit}",
            ip_address=None
        )

        return movement

    @staticmethod
    @transaction.atomic
    def adjust_stock(part_id, new_quantity, notes, user):
        """
        调整库存

        Args:
            part_id: 部件ID
            new_quantity: 新库存数量
            notes: 备注
            user: 操作人

        Returns:
            PartsMovement: 流水记录
        """
        part = Part.objects.get(id=part_id)
        old_quantity = part.current_stock

        movement = PartsMovement.objects.create(
            part=part,
            type='adjustment',
            quantity=new_quantity,
            related_doc='',
            notes=f"{notes}（原库存：{old_quantity}）",
            operator=user
        )

        AuditLog.objects.create(
            user=user,
            action='update',
            module='部件',
            target=part.name,
            details=f"调整库存：{old_quantity} → {new_quantity} {part.unit}",
            ip_address=None
        )

        return movement

    @staticmethod
    def get_low_stock_parts():
        """获取库存不足的部件"""
        from django.db.models import F
        return Part.objects.filter(
            is_active=True,
            current_stock__lt=F('safety_stock')
        ).order_by('current_stock')
