"""
库存单套追踪服务
"""
from typing import List
from django.db import transaction
from django.db.models import Max

from ..models import InventoryUnit, UnitMovement, SKU, Order


class InventoryUnitService:
    """库存单套服务（低侵入封装）"""

    UNIT_PREFIX = "ZSY"

    @staticmethod
    def _build_unit_no(sku_code: str, seq: int) -> str:
        return f"{InventoryUnitService.UNIT_PREFIX}-{sku_code}-{seq:04d}"

    @staticmethod
    def _extract_seq(unit_no: str) -> int:
        try:
            return int((unit_no or "").split("-")[-1])
        except Exception:
            return 0

    @staticmethod
    @transaction.atomic
    def ensure_units_for_sku(sku: SKU) -> int:
        """
        按 SKU 总库存补齐 InventoryUnit（只增不减，避免破坏历史链路）
        返回本次新增数量
        """
        existing_count = InventoryUnit.objects.filter(sku=sku).count()
        if existing_count >= sku.stock:
            return 0

        current_max_no = InventoryUnit.objects.filter(sku=sku).aggregate(mx=Max("unit_no"))["mx"] or ""
        seq = max(InventoryUnitService._extract_seq(current_max_no), existing_count)
        created = 0
        for _ in range(sku.stock - existing_count):
            seq += 1
            InventoryUnit.objects.create(
                sku=sku,
                unit_no=InventoryUnitService._build_unit_no(sku.code, seq),
                status="in_warehouse",
                current_location_type="warehouse",
                is_active=True,
            )
            created += 1
        return created

    @staticmethod
    @transaction.atomic
    def bootstrap_all_units() -> int:
        """按所有启用 SKU 一次性补齐单套数据"""
        created = 0
        for sku in SKU.objects.filter(is_active=True):
            created += InventoryUnitService.ensure_units_for_sku(sku)
        return created

    @staticmethod
    @transaction.atomic
    def allocate_from_warehouse(order: Order, sku: SKU, quantity: int, tracking_no: str, operator=None) -> int:
        """
        仓库发货：从在库单套中分配给订单
        返回成功分配数量（不足时只做告警节点，不抛出异常）
        """
        InventoryUnitService.ensure_units_for_sku(sku)
        units = list(
            InventoryUnit.objects.filter(
                sku=sku,
                is_active=True,
                status="in_warehouse",
                current_order__isnull=True,
            ).order_by("unit_no")[: max(quantity, 0)]
        )

        for unit in units:
            unit.status = "in_transit"
            unit.current_order = order
            unit.current_location_type = "transit"
            unit.last_tracking_no = tracking_no or unit.last_tracking_no
            unit.save(update_fields=["status", "current_order", "current_location_type", "last_tracking_no", "updated_at"])
            UnitMovement.objects.create(
                unit=unit,
                event_type="WAREHOUSE_OUT",
                status="normal",
                to_order=order,
                tracking_no=tracking_no or "",
                notes="仓库发货",
                operator=operator,
            )

        shortage = max(int(quantity) - len(units), 0)
        if shortage > 0:
            for _ in range(shortage):
                placeholder = InventoryUnit.objects.filter(sku=sku, is_active=True).order_by("unit_no").first()
                if not placeholder:
                    break
                UnitMovement.objects.create(
                    unit=placeholder,
                    event_type="EXCEPTION",
                    status="warning",
                    to_order=order,
                    tracking_no=tracking_no or "",
                    notes=f"仓库分配不足：期望{quantity}，实际{len(units)}",
                    operator=operator,
                )

        return len(units)

    @staticmethod
    @transaction.atomic
    def transfer_to_target(source_order: Order, target_order: Order, sku: SKU, quantity: int, tracking_no: str, transfer=None, operator=None) -> int:
        """
        转寄完成：来源订单单套切换到目标订单
        返回成功切换数量
        """
        units = list(
            InventoryUnit.objects.filter(
                sku=sku,
                is_active=True,
                current_order=source_order,
            ).exclude(status__in=["scrapped"]).order_by("unit_no")[: max(quantity, 0)]
        )
        moved = 0
        for unit in units:
            UnitMovement.objects.create(
                unit=unit,
                event_type="TRANSFER_SHIPPED",
                status="normal",
                from_order=source_order,
                to_order=target_order,
                transfer=transfer,
                tracking_no=tracking_no or "",
                notes="转寄寄出",
                operator=operator,
            )
            unit.current_order = target_order
            unit.status = "in_transit"
            unit.current_location_type = "transit"
            unit.last_tracking_no = tracking_no or unit.last_tracking_no
            unit.save(update_fields=["current_order", "status", "current_location_type", "last_tracking_no", "updated_at"])
            UnitMovement.objects.create(
                unit=unit,
                event_type="TRANSFER_COMPLETED",
                status="normal",
                from_order=source_order,
                to_order=target_order,
                transfer=transfer,
                tracking_no=tracking_no or "",
                notes="转寄完成并挂靠到目标订单",
                operator=operator,
            )
            moved += 1

        if moved < int(quantity):
            placeholder = InventoryUnit.objects.filter(sku=sku, is_active=True).order_by("unit_no").first()
            if placeholder:
                UnitMovement.objects.create(
                    unit=placeholder,
                    event_type="EXCEPTION",
                    status="warning",
                    from_order=source_order,
                    to_order=target_order,
                    transfer=transfer,
                    tracking_no=tracking_no or "",
                    notes=f"转寄单套不足：期望{quantity}，实际{moved}",
                    operator=operator,
                )
        return moved

    @staticmethod
    @transaction.atomic
    def return_to_warehouse(order: Order, tracking_no: str, operator=None) -> int:
        """
        标记归还：订单单套回仓
        返回回仓数量
        """
        units: List[InventoryUnit] = list(
            InventoryUnit.objects.filter(
                current_order=order,
                is_active=True,
            ).exclude(status="scrapped")
        )
        for unit in units:
            UnitMovement.objects.create(
                unit=unit,
                event_type="RETURNED_WAREHOUSE",
                status="closed",
                from_order=order,
                tracking_no=tracking_no or "",
                notes="订单归还入仓",
                operator=operator,
            )
            unit.status = "in_warehouse"
            unit.current_order = None
            unit.current_location_type = "warehouse"
            unit.last_tracking_no = tracking_no or unit.last_tracking_no
            unit.save(update_fields=["status", "current_order", "current_location_type", "last_tracking_no", "updated_at"])
        return len(units)
