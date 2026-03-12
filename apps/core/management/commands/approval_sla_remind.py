import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import ApprovalTask
from apps.core.services import ApprovalService, AuditService, NotificationService
from apps.core.utils import get_system_settings


class Command(BaseCommand):
    help = '审批SLA催办任务：批量催办超时待审批任务。'

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=0, help='覆盖系统阈值（小时），0=使用系统设置')
        parser.add_argument('--limit', type=int, default=100, help='单次最多催办数量')
        parser.add_argument('--json', action='store_true', help='JSON输出')
        parser.add_argument('--dry-run', action='store_true', help='仅预览不执行')
        parser.add_argument('--notify', action='store_true', help='发送催办告警通知（受系统通知设置控制）')

    def handle(self, *args, **options):
        dry_run = bool(options.get('dry_run'))
        as_json = bool(options.get('json'))
        limit = max(int(options.get('limit') or 100), 1)
        custom_hours = int(options.get('hours') or 0)
        notify = bool(options.get('notify'))

        settings = get_system_settings()
        warn_hours = custom_hours if custom_hours > 0 else int(settings.get('approval_pending_warn_hours', 24) or 24)
        cutoff = timezone.now() - timedelta(hours=warn_hours)

        overdue_tasks = list(
            ApprovalTask.objects.filter(status='pending', created_at__lt=cutoff)
            .order_by('created_at')[:limit]
        )

        reminded = []
        if not dry_run:
            for task in overdue_tasks:
                task = ApprovalService.remind_pending_task(task.id)
                reminded.append(task.task_no)
                AuditService.log_with_diff(
                    user=None,
                    action='status_change',
                    module='审批',
                    target=task.task_no,
                    summary='SLA自动催办',
                    before={},
                    after={
                        'remind_count': int(task.remind_count or 0),
                        'last_reminded_at': task.last_reminded_at.isoformat() if task.last_reminded_at else '',
                    },
                    extra={'source': 'command', 'task': 'approval_sla_remind'},
                )

        payload = {
            'mode': 'dry_run' if dry_run else 'apply',
            'warn_hours': warn_hours,
            'total_overdue': len(overdue_tasks),
            'reminded_count': 0 if dry_run else len(reminded),
            'task_nos': [t.task_no for t in overdue_tasks],
        }

        if notify and overdue_tasks:
            alerts = [{
                'source': 'approval',
                'severity': 'danger',
                'title': '审批任务超时',
                'value': len(overdue_tasks),
                'desc': f'待审批超过 {warn_hours} 小时',
            }]
            payload['notify'] = NotificationService.notify_alerts(
                title='审批SLA催办告警',
                alerts=alerts,
                settings=settings,
                source='approval_sla_remind',
            )

        if as_json:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
            return

        self.stdout.write(
            f"[approval_sla_remind] mode={payload['mode']} warn_hours={warn_hours} "
            f"overdue={payload['total_overdue']} reminded={payload['reminded_count']}"
        )
