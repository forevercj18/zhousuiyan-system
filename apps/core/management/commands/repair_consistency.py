import json

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Sum

from apps.core.models import SKU, TransferAllocation
from apps.core.utils import (
    run_data_consistency_checks,
    build_data_consistency_repair_plan,
    persist_data_consistency_check_result,
)


class Command(BaseCommand):
    help = '一致性修复工具：默认仅预览（dry-run），加 --apply 执行可自动修复项。'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='执行自动修复项（默认仅预览）')
        parser.add_argument('--json', action='store_true', help='JSON输出')
        parser.add_argument('--save', action='store_true', help='保存执行台账')
        parser.add_argument(
            '--fix-duplicate-locked',
            action='store_true',
            help='启用“重复锁合并”修复（同 source->target->sku 的多条 locked 合并）'
        )

    def handle(self, *args, **options):
        apply_changes = bool(options.get('apply'))
        as_json = bool(options.get('json'))
        save_flag = bool(options.get('save'))
        fix_duplicate_locked = bool(options.get('fix_duplicate_locked'))

        check_result = run_data_consistency_checks()
        plan = build_data_consistency_repair_plan(check_result)
        auto_repairs = plan.get('auto_repairs') or []
        manual_items = plan.get('manual_items') or []
        applied_count = 0
        applied_items = []

        if apply_changes and auto_repairs:
            with transaction.atomic():
                for item in auto_repairs:
                    if item.get('type') != 'legacy_stock_mismatch':
                        continue
                    sku_id = item.get('sku_id')
                    target_stock = int(item.get('to_stock') or 0)
                    sku = SKU.objects.filter(id=sku_id).first()
                    if not sku:
                        continue
                    if int(sku.stock or 0) == target_stock:
                        continue
                    sku.stock = target_stock
                    sku.save(update_fields=['stock'])
                    applied_count += 1
                    applied_items.append({
                            'type': 'legacy_stock_mismatch',
                            'sku_id': sku.id,
                            'sku_code': sku.code,
                            'new_stock': target_stock,
                    })
                if fix_duplicate_locked:
                    duplicate_groups = (
                        TransferAllocation.objects.select_for_update()
                        .filter(status='locked')
                        .values('source_order_id', 'target_order_id', 'sku_id')
                        .annotate(row_count=Count('id'), quantity_total=Sum('quantity'))
                        .filter(row_count__gt=1)
                    )
                    for g in duplicate_groups:
                        rows = list(
                            TransferAllocation.objects.filter(
                                source_order_id=g['source_order_id'],
                                target_order_id=g['target_order_id'],
                                sku_id=g['sku_id'],
                                status='locked',
                            ).order_by('created_at', 'id')
                        )
                        if len(rows) <= 1:
                            continue
                        keep = rows[0]
                        total_qty = sum(int(r.quantity or 0) for r in rows)
                        if int(keep.quantity or 0) != total_qty:
                            keep.quantity = total_qty
                            keep.save(update_fields=['quantity', 'updated_at'])
                        for r in rows[1:]:
                            r.status = 'released'
                            r.save(update_fields=['status', 'updated_at'])
                        applied_count += 1
                        applied_items.append({
                            'type': 'duplicate_locked_allocations',
                            'source_order_id': g['source_order_id'],
                            'target_order_id': g['target_order_id'],
                            'sku_id': g['sku_id'],
                            'kept_id': keep.id,
                            'released_count': max(len(rows) - 1, 0),
                            'total_qty': total_qty,
                        })

        payload = {
            'mode': 'apply' if apply_changes else 'dry_run',
            'check': {
                'total_issues': int(check_result.get('total_issues') or 0),
                'error_count': int(check_result.get('error_count') or 0),
                'warning_count': int(check_result.get('warning_count') or 0),
            },
            'plan': {
                'auto_repair_count': len(auto_repairs),
                'manual_count': len(manual_items),
                'auto_repairs': auto_repairs,
                'manual_items': manual_items,
                'fix_duplicate_locked_enabled': fix_duplicate_locked,
            },
            'result': {
                'applied_count': applied_count,
                'applied_items': applied_items,
            },
        }

        if save_flag:
            persist_data_consistency_check_result(
                {
                    'total_issues': payload['check']['total_issues'],
                    'error_count': payload['check']['error_count'],
                    'warning_count': payload['check']['warning_count'],
                    'issues': check_result.get('issues') or [],
                },
                executed_by=None,
                source='repair_apply' if apply_changes else 'repair_dry_run',
            )

        if as_json:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
            return

        self.stdout.write(f"[模式] {payload['mode']}")
        self.stdout.write(
            f"[巡检] 问题总数={payload['check']['total_issues']} "
            f"(error={payload['check']['error_count']}, warning={payload['check']['warning_count']})"
        )
        self.stdout.write(f"[计划] 自动修复={payload['plan']['auto_repair_count']} 人工处理={payload['plan']['manual_count']}")
        if apply_changes:
            self.stdout.write(f"[结果] 已应用={payload['result']['applied_count']}")
        else:
            self.stdout.write('[结果] 当前为预览模式，未写入任何修复。')
