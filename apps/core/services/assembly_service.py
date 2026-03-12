"""
装配与维修工单服务
"""
from django.db import transaction
from django.utils import timezone

from .audit_service import AuditService
from .inventory_unit_service import InventoryUnitService
from .procurement_service import PartsService
from ..models import (
    AssemblyOrder,
    AssemblyOrderItem,
    InventoryUnitPart,
    MaintenanceWorkOrder,
    MaintenanceWorkOrderItem,
    UnitDisposalOrder,
    UnitDisposalOrderItem,
    PartRecoveryInspection,
    SKU,
    SKUComponent,
    InventoryUnit,
    UnitMovement,
)


class AssemblyService:
    """SKU 装配服务：通过部件扣减生成套餐库存。"""

    @staticmethod
    @transaction.atomic
    def create_and_complete_assembly(*, sku: SKU, quantity: int, notes: str = '', user=None) -> AssemblyOrder:
        quantity = int(quantity or 0)
        if quantity <= 0:
            raise ValueError('装配套数必须大于 0')

        components = list(SKUComponent.objects.select_related('part').filter(sku=sku).order_by('part__name'))
        if not components:
            raise ValueError('该套餐未配置部件清单，不能新增库存')

        for comp in components:
            required_quantity = int(comp.quantity_per_set or 0) * quantity
            if required_quantity <= 0:
                raise ValueError(f'部件 {comp.part.name} 的单套用量无效')
            if int(comp.part.current_stock or 0) < required_quantity:
                raise ValueError(f'部件 {comp.part.name} 库存不足，需 {required_quantity}，现有 {comp.part.current_stock}')

        assembly = AssemblyOrder.objects.create(
            sku=sku,
            quantity=quantity,
            status='draft',
            notes=notes or '',
            created_by=user,
        )

        order_items = []
        for comp in components:
            required_quantity = int(comp.quantity_per_set or 0) * quantity
            PartsService.outbound(
                comp.part.id,
                required_quantity,
                assembly.assembly_no,
                f'SKU装配：{sku.code} x {quantity}',
                user,
            )
            order_items.append(
                AssemblyOrderItem(
                    assembly_order=assembly,
                    part=comp.part,
                    quantity_per_set=int(comp.quantity_per_set or 1),
                    required_quantity=required_quantity,
                    deducted_quantity=required_quantity,
                    notes=comp.notes or '',
                )
            )
        if order_items:
            AssemblyOrderItem.objects.bulk_create(order_items)

        created_units = InventoryUnitService.create_units_for_sku(sku, quantity, source_assembly_order=assembly)
        assembly.status = 'completed'
        assembly.completed_at = timezone.now()
        assembly.save(update_fields=['status', 'completed_at', 'updated_at'])

        AuditService.log_with_diff(
            user=user,
            action='create',
            module='产品管理',
            target=assembly.assembly_no,
            summary='执行套餐装配新增库存',
            before={},
            after={
                'assembly_no': assembly.assembly_no,
                'sku_id': sku.id,
                'sku_code': sku.code,
                'quantity': quantity,
                'created_units': created_units,
                'status': assembly.status,
            },
            extra={
                'sku_id': sku.id,
                'created_units': created_units,
                'notes': notes or '',
            },
        )
        return assembly

    @staticmethod
    @transaction.atomic
    def cancel_assembly(*, assembly: AssemblyOrder, user=None) -> AssemblyOrder:
        if assembly.status != 'completed':
            raise ValueError('仅已完成装配单可以取消')

        created_units = list(
            assembly.created_units.select_related('sku').prefetch_related('movements').order_by('unit_no')
        )
        if not created_units:
            raise ValueError('该装配单未关联任何单套，不能取消')

        for unit in created_units:
            if unit.status != 'in_warehouse' or unit.current_order_id or not unit.is_active:
                raise ValueError(f'单套 {unit.unit_no} 已进入业务流转，不能取消装配')
            if unit.movements.exists():
                raise ValueError(f'单套 {unit.unit_no} 已存在流转节点，不能取消装配')

        for item in assembly.items.select_related('part').all():
            if int(item.deducted_quantity or 0) > 0:
                PartsService.inbound(
                    item.part_id,
                    int(item.deducted_quantity or 0),
                    assembly.assembly_no,
                    f'取消装配回滚：{assembly.sku.code}',
                    user,
                )

        unit_nos = [unit.unit_no for unit in created_units]
        InventoryUnit.objects.filter(id__in=[u.id for u in created_units]).update(
            status='scrapped',
            current_location_type='warehouse',
            is_active=False,
            current_order=None,
            updated_at=timezone.now(),
        )
        assembly.status = 'cancelled'
        assembly.save(update_fields=['status', 'updated_at'])
        InventoryUnitService.sync_legacy_stock_field(assembly.sku)
        AuditService.log_with_diff(
            user=user,
            action='update',
            module='装配单',
            target=assembly.assembly_no,
            summary='取消装配单并回滚部件',
            before={'status': 'completed'},
            after={'status': assembly.status, 'cancelled_units': unit_nos},
            extra={'sku_id': assembly.sku_id, 'unit_count': len(unit_nos)},
        )
        return assembly


class MaintenanceService:
    """单套维修换件服务。"""

    @staticmethod
    @transaction.atomic
    def create_work_order(*, unit: InventoryUnit, issue_desc: str, items: list[dict], notes: str = '', user=None) -> MaintenanceWorkOrder:
        if not unit.is_active:
            raise ValueError('单套已停用，不能创建维修工单')
        if unit.status == 'scrapped':
            raise ValueError('报废单套不能创建维修工单')
        if MaintenanceWorkOrder.objects.filter(unit=unit, status='draft').exists():
            raise ValueError('该单套已有待执行维修工单，请先处理')
        if not items:
            raise ValueError('请至少填写 1 条换件明细')

        work_order = MaintenanceWorkOrder.objects.create(
            unit=unit,
            sku=unit.sku,
            issue_desc=issue_desc or '',
            status='draft',
            notes=notes or '',
            created_by=user,
        )
        unit.status = 'maintenance'
        unit.current_location_type = 'warehouse'
        unit.save(update_fields=['status', 'current_location_type', 'updated_at'])
        UnitMovement.objects.create(
            unit=unit,
            event_type='MAINTENANCE_CREATED',
            status='warning',
            notes=f'维修工单创建：{work_order.work_order_no}',
            operator=user,
        )
        rows = []
        for item in items:
            qty = int(item.get('replace_quantity') or 0)
            if qty <= 0:
                raise ValueError('更换数量必须大于 0')
            rows.append(
                MaintenanceWorkOrderItem(
                    work_order=work_order,
                    old_part_id=int(item['old_part_id']),
                    new_part_id=int(item['new_part_id']),
                    replace_quantity=qty,
                    notes=item.get('notes', '')[:200],
                )
            )
        MaintenanceWorkOrderItem.objects.bulk_create(rows)
        AuditService.log_with_diff(
            user=user,
            action='create',
            module='维修工单',
            target=work_order.work_order_no,
            summary='创建维修换件工单',
            before={},
            after={
                'work_order_no': work_order.work_order_no,
                'unit_no': unit.unit_no,
                'sku_code': unit.sku.code,
                'status': work_order.status,
                'item_count': len(rows),
            },
            extra={'unit_id': unit.id, 'sku_id': unit.sku_id},
        )
        return work_order

    @staticmethod
    @transaction.atomic
    def complete_work_order(*, work_order: MaintenanceWorkOrder, user=None) -> MaintenanceWorkOrder:
        if work_order.status != 'draft':
            raise ValueError('仅草稿工单可以执行')

        unit = work_order.unit
        if unit.status == 'scrapped':
            raise ValueError('报废单套不能执行维修')

        before_rows = []
        after_rows = []
        for item in work_order.items.select_related('old_part', 'new_part').all():
            PartsService.outbound(
                item.new_part_id,
                item.replace_quantity,
                work_order.work_order_no,
                f'维修换件：{unit.unit_no}',
                user,
            )

            old_row = InventoryUnitPart.objects.filter(unit=unit, part=item.old_part, is_active=True).first()
            if old_row:
                before_rows.append({
                    'part_id': old_row.part_id,
                    'expected_quantity': int(old_row.expected_quantity or 0),
                    'actual_quantity': int(old_row.actual_quantity or 0),
                    'status': old_row.status,
                })
                old_row.actual_quantity = max(int(old_row.actual_quantity or 0) - item.replace_quantity, 0)
                old_row.status = 'damaged' if old_row.actual_quantity > 0 else 'missing'
                old_row.notes = (old_row.notes or '') + f'；工单{work_order.work_order_no}更换{item.replace_quantity}'
                old_row.save(update_fields=['actual_quantity', 'status', 'notes', 'updated_at'])
                after_rows.append({
                    'part_id': old_row.part_id,
                    'expected_quantity': int(old_row.expected_quantity or 0),
                    'actual_quantity': int(old_row.actual_quantity or 0),
                    'status': old_row.status,
                })

            new_row = InventoryUnitPart.objects.filter(unit=unit, part=item.new_part, is_active=True).first()
            if new_row:
                before_rows.append({
                    'part_id': new_row.part_id,
                    'expected_quantity': int(new_row.expected_quantity or 0),
                    'actual_quantity': int(new_row.actual_quantity or 0),
                    'status': new_row.status,
                })
                new_row.actual_quantity = int(new_row.actual_quantity or 0) + item.replace_quantity
                new_row.expected_quantity = max(int(new_row.expected_quantity or 0), new_row.actual_quantity)
                new_row.status = 'normal'
                new_row.notes = item.notes or new_row.notes
                new_row.last_checked_at = timezone.now()
                new_row.save(update_fields=['actual_quantity', 'expected_quantity', 'status', 'notes', 'last_checked_at', 'updated_at'])
            else:
                new_row = InventoryUnitPart.objects.create(
                    unit=unit,
                    part=item.new_part,
                    expected_quantity=item.replace_quantity,
                    actual_quantity=item.replace_quantity,
                    status='normal',
                    notes=item.notes or '',
                    is_active=True,
                    last_checked_at=timezone.now(),
                )
            after_rows.append({
                'part_id': new_row.part_id,
                'expected_quantity': int(new_row.expected_quantity or 0),
                'actual_quantity': int(new_row.actual_quantity or 0),
                'status': new_row.status,
            })

        unit.status = 'in_warehouse'
        unit.current_location_type = 'warehouse'
        unit.save(update_fields=['status', 'current_location_type', 'updated_at'])

        work_order.status = 'completed'
        work_order.completed_by = user
        work_order.completed_at = timezone.now()
        work_order.save(update_fields=['status', 'completed_by', 'completed_at', 'updated_at'])
        UnitMovement.objects.create(
            unit=unit,
            event_type='MAINTENANCE_COMPLETED',
            status='normal',
            notes=f'维修工单完成：{work_order.work_order_no}',
            operator=user,
        )

        AuditService.log_with_diff(
            user=user,
            action='update',
            module='维修工单',
            target=work_order.work_order_no,
            summary='执行维修换件工单',
            before={'items': before_rows},
            after={'items': after_rows},
            extra={'unit_id': unit.id, 'sku_id': unit.sku_id},
        )
        return work_order

    @staticmethod
    @transaction.atomic
    def cancel_work_order(*, work_order: MaintenanceWorkOrder, user=None) -> MaintenanceWorkOrder:
        if work_order.status != 'draft':
            raise ValueError('仅草稿维修工单可以取消')
        unit = work_order.unit
        work_order.status = 'cancelled'
        work_order.save(update_fields=['status', 'updated_at'])
        if not MaintenanceWorkOrder.objects.filter(unit=unit, status='draft').exclude(id=work_order.id).exists():
            unit.status = 'in_warehouse'
            unit.current_location_type = 'warehouse'
            unit.save(update_fields=['status', 'current_location_type', 'updated_at'])
        AuditService.log_with_diff(
            user=user,
            action='update',
            module='维修工单',
            target=work_order.work_order_no,
            summary='取消维修工单',
            before={'status': 'draft'},
            after={'status': 'cancelled'},
            extra={'unit_id': unit.id, 'sku_id': unit.sku_id},
        )
        return work_order

    @staticmethod
    @transaction.atomic
    def reverse_work_order(*, work_order: MaintenanceWorkOrder, user=None) -> MaintenanceWorkOrder:
        if work_order.status != 'completed':
            raise ValueError('仅已完成维修工单可以冲销')

        unit = work_order.unit
        if not unit.is_active:
            raise ValueError('单套已停用，不能冲销维修工单')
        if unit.current_order_id:
            raise ValueError('单套已重新进入订单流转，不能冲销维修工单')
        if unit.status != 'in_warehouse':
            raise ValueError('仅在库单套可冲销维修工单')
        if MaintenanceWorkOrder.objects.filter(unit=unit, status='draft').exclude(id=work_order.id).exists():
            raise ValueError('该单套存在待执行维修工单，不能冲销')
        if MaintenanceWorkOrder.objects.filter(
            unit=unit,
            status='completed',
            completed_at__gt=work_order.completed_at,
        ).exclude(id=work_order.id).exists():
            raise ValueError('该单套已有后续维修记录，不能冲销')

        before_rows = []
        after_rows = []
        for item in work_order.items.select_related('old_part', 'new_part').all():
            PartsService.inbound(
                item.new_part_id,
                item.replace_quantity,
                work_order.work_order_no,
                f'维修工单冲销回库：{unit.unit_no}',
                user,
            )

            new_row = InventoryUnitPart.objects.filter(unit=unit, part=item.new_part, is_active=True).first()
            if not new_row or int(new_row.actual_quantity or 0) < item.replace_quantity:
                raise ValueError(f'单套当前部件状态异常，无法冲销：{item.new_part.name}')
            before_rows.append({
                'part_id': new_row.part_id,
                'expected_quantity': int(new_row.expected_quantity or 0),
                'actual_quantity': int(new_row.actual_quantity or 0),
                'status': new_row.status,
            })
            new_row.actual_quantity = int(new_row.actual_quantity or 0) - item.replace_quantity
            if new_row.actual_quantity <= 0 and int(new_row.expected_quantity or 0) <= item.replace_quantity:
                new_row.is_active = False
                new_row.actual_quantity = 0
                new_row.status = 'missing'
                new_row.save(update_fields=['is_active', 'actual_quantity', 'status', 'updated_at'])
            else:
                if new_row.actual_quantity >= int(new_row.expected_quantity or 0):
                    new_row.status = 'normal'
                elif new_row.actual_quantity <= 0:
                    new_row.status = 'missing'
                else:
                    new_row.status = 'damaged'
                new_row.save(update_fields=['actual_quantity', 'status', 'updated_at'])
            after_rows.append({
                'part_id': new_row.part_id,
                'expected_quantity': int(new_row.expected_quantity or 0),
                'actual_quantity': int(new_row.actual_quantity or 0),
                'status': new_row.status,
                'is_active': new_row.is_active,
            })

            old_row = InventoryUnitPart.objects.filter(unit=unit, part=item.old_part, is_active=True).first()
            if old_row:
                before_rows.append({
                    'part_id': old_row.part_id,
                    'expected_quantity': int(old_row.expected_quantity or 0),
                    'actual_quantity': int(old_row.actual_quantity or 0),
                    'status': old_row.status,
                })
                old_row.actual_quantity = int(old_row.actual_quantity or 0) + item.replace_quantity
                old_row.expected_quantity = max(int(old_row.expected_quantity or 0), old_row.actual_quantity)
                old_row.status = 'normal' if old_row.actual_quantity >= int(old_row.expected_quantity or 0) else 'damaged'
                old_row.last_checked_at = timezone.now()
                old_row.save(update_fields=['actual_quantity', 'expected_quantity', 'status', 'last_checked_at', 'updated_at'])
            else:
                old_row = InventoryUnitPart.objects.create(
                    unit=unit,
                    part=item.old_part,
                    expected_quantity=item.replace_quantity,
                    actual_quantity=item.replace_quantity,
                    status='normal',
                    notes=f'工单{work_order.work_order_no}冲销恢复',
                    is_active=True,
                    last_checked_at=timezone.now(),
                )
            after_rows.append({
                'part_id': old_row.part_id,
                'expected_quantity': int(old_row.expected_quantity or 0),
                'actual_quantity': int(old_row.actual_quantity or 0),
                'status': old_row.status,
                'is_active': old_row.is_active,
            })

        work_order.status = 'reversed'
        work_order.save(update_fields=['status', 'updated_at'])
        UnitMovement.objects.create(
            unit=unit,
            event_type='MAINTENANCE_REVERSED',
            status='warning',
            notes=f'维修工单冲销：{work_order.work_order_no}',
            operator=user,
        )
        AuditService.log_with_diff(
            user=user,
            action='update',
            module='维修工单',
            target=work_order.work_order_no,
            summary='冲销维修换件工单',
            before={'status': 'completed', 'items': before_rows},
            after={'status': 'reversed', 'items': after_rows},
            extra={'unit_id': unit.id, 'sku_id': unit.sku_id},
        )
        return work_order


class UnitDisposalService:
    """单套拆解/报废服务。"""

    @staticmethod
    def _ensure_unit_can_dispose(unit: InventoryUnit):
        if not unit.is_active:
            raise ValueError('单套已停用，不能重复处置')
        if unit.current_order_id:
            raise ValueError('单套仍绑定订单，不能处置')
        if unit.status not in ['in_warehouse', 'maintenance']:
            raise ValueError('仅在库或维修中的单套可处置')
        if MaintenanceWorkOrder.objects.filter(unit=unit, status='draft').exists():
            raise ValueError('该单套仍有待执行维修工单，不能处置')

    @staticmethod
    @transaction.atomic
    def create_and_complete(*, unit: InventoryUnit, action_type: str, issue_desc: str = '', notes: str = '', user=None) -> UnitDisposalOrder:
        if action_type not in ['disassemble', 'scrap']:
            raise ValueError('不支持的处置类型')
        UnitDisposalService._ensure_unit_can_dispose(unit)

        order = UnitDisposalOrder.objects.create(
            action_type=action_type,
            unit=unit,
            sku=unit.sku,
            status='draft',
            issue_desc=issue_desc or '',
            notes=notes or '',
            created_by=user,
        )

        rows = []
        inspection_rows = []
        active_rows = list(
            InventoryUnitPart.objects.filter(unit=unit, is_active=True).select_related('part').order_by('part__name')
        )
        for row in active_rows:
            actual_qty = int(row.actual_quantity or 0)
            returned_qty = actual_qty if action_type == 'disassemble' and actual_qty > 0 else 0
            rows.append(
                UnitDisposalOrderItem(
                    disposal_order=order,
                    part=row.part,
                    quantity=actual_qty,
                    returned_quantity=returned_qty,
                    notes=row.notes or '',
                )
            )
            row.is_active = False
            row.save(update_fields=['is_active', 'updated_at'])
        created_items = UnitDisposalOrderItem.objects.bulk_create(rows) if rows else []
        if action_type == 'disassemble':
            for item in created_items:
                if int(item.returned_quantity or 0) > 0:
                    inspection_rows.append(
                        PartRecoveryInspection(
                            disposal_order=order,
                            disposal_item=item,
                            unit=unit,
                            sku=unit.sku,
                            part=item.part,
                            quantity=int(item.returned_quantity or 0),
                            status='pending',
                            notes=item.notes or '',
                        )
                    )
        if inspection_rows:
            PartRecoveryInspection.objects.bulk_create(inspection_rows)

        unit.status = 'scrapped'
        unit.current_location_type = 'warehouse'
        unit.current_order = None
        unit.is_active = False
        unit.save(update_fields=['status', 'current_location_type', 'current_order', 'is_active', 'updated_at'])
        UnitMovement.objects.create(
            unit=unit,
            event_type='UNIT_DISASSEMBLED' if action_type == 'disassemble' else 'UNIT_SCRAPPED',
            status='closed',
            notes=f'单套处置：{order.get_action_type_display()} {order.disposal_no}',
            operator=user,
        )
        order.status = 'completed'
        order.completed_by = user
        order.completed_at = timezone.now()
        order.save(update_fields=['status', 'completed_by', 'completed_at', 'updated_at'])
        InventoryUnitService.sync_legacy_stock_field(unit.sku)
        AuditService.log_with_diff(
            user=user,
            action='update',
            module='单套处置',
            target=order.disposal_no,
            summary=f'执行单套{order.get_action_type_display()}',
            before={'unit_status': 'in_warehouse'},
            after={'unit_status': unit.status, 'is_active': unit.is_active},
            extra={'unit_id': unit.id, 'sku_id': unit.sku_id, 'action_type': action_type},
        )
        return order

    @staticmethod
    @transaction.atomic
    def process_recovery_inspection(*, inspection: PartRecoveryInspection, action_type: str, notes: str = '', user=None) -> PartRecoveryInspection:
        if inspection.status == 'pending':
            allowed_actions = ['returned', 'repair', 'scrapped']
            inbound_reason = f'拆解回件质检通过回库：{inspection.unit.unit_no}'
            before_status = 'pending'
        elif inspection.status == 'repair':
            allowed_actions = ['returned', 'scrapped']
            inbound_reason = f'拆解回件维修完成回库：{inspection.unit.unit_no}'
            before_status = 'repair'
        else:
            raise ValueError('当前回件状态不允许继续处理')

        if action_type not in allowed_actions:
            raise ValueError('不支持的质检处理结果')

        if action_type == 'returned':
            PartsService.inbound(
                inspection.part_id,
                inspection.quantity,
                inspection.disposal_order.disposal_no,
                inbound_reason,
                user,
            )

        inspection.status = action_type
        inspection.notes = notes or inspection.notes
        inspection.processed_by = user
        inspection.processed_at = timezone.now()
        inspection.save(update_fields=['status', 'notes', 'processed_by', 'processed_at', 'updated_at'])
        AuditService.log_with_diff(
            user=user,
            action='update',
            module='回件质检',
            target=f'{inspection.disposal_order.disposal_no}:{inspection.part.name}',
            summary='处理拆解回件质检',
            before={'status': before_status},
            after={'status': inspection.status},
            extra={
                'inspection_id': inspection.id,
                'part_id': inspection.part_id,
                'unit_id': inspection.unit_id,
                'quantity': inspection.quantity,
            },
        )
        return inspection
