import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import Transfer, ApprovalTask, RiskEvent, DataConsistencyCheckRun
from apps.core.services import AuditService, NotificationService
from apps.core.utils import get_system_settings


class Command(BaseCommand):
    help = '运维告警聚合任务：用于定时巡检告警，支持 JSON 输出与审计落库。'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='JSON输出')
        parser.add_argument('--source', type=str, default='', help='来源过滤：transfer/approval/risk/consistency/finance')
        parser.add_argument('--severity', type=str, default='', help='级别过滤：danger/warning/info')
        parser.add_argument('--save-audit', action='store_true', help='保存运维巡检审计日志')
        parser.add_argument('--notify', action='store_true', help='发送告警通知（受系统通知设置控制）')

    def handle(self, *args, **options):
        source_filter = (options.get('source') or '').strip()
        severity_filter = (options.get('severity') or '').strip()
        as_json = bool(options.get('json'))
        save_audit = bool(options.get('save_audit'))
        notify = bool(options.get('notify'))

        settings = get_system_settings()
        transfer_pending_timeout_hours = int(settings.get('transfer_pending_timeout_hours', 24) or 24)
        approval_pending_warn_hours = int(settings.get('approval_pending_warn_hours', 24) or 24)
        now = timezone.now()

        transfer_overdue_count = Transfer.objects.filter(
            status='pending',
            created_at__lt=now - timedelta(hours=transfer_pending_timeout_hours),
        ).count()
        approval_overdue_count = ApprovalTask.objects.filter(
            status='pending',
            created_at__lt=now - timedelta(hours=approval_pending_warn_hours),
        ).count()
        open_risk_count = RiskEvent.objects.filter(status='open').count()
        latest_check = DataConsistencyCheckRun.objects.order_by('-created_at').first()
        latest_check_issues = int(latest_check.total_issues) if latest_check else 0
        latest_check_type_counts = (latest_check.summary or {}).get('type_counts', {}) if latest_check else {}
        finance_mismatch_count = 0
        if latest_check:
            if isinstance(latest_check_type_counts, dict):
                finance_mismatch_count = int(latest_check_type_counts.get('finance_reconciliation_mismatch', 0) or 0)
            elif latest_check.issues:
                finance_mismatch_count = sum(
                    1 for i in (latest_check.issues or [])
                    if (i or {}).get('type') == 'finance_reconciliation_mismatch'
                )

        alerts = []
        if transfer_overdue_count > 0:
            alerts.append({
                'source': 'transfer',
                'severity': 'danger',
                'title': '转寄任务超时',
                'value': transfer_overdue_count,
                'desc': f'待执行超过 {transfer_pending_timeout_hours} 小时',
            })
        if approval_overdue_count > 0:
            alerts.append({
                'source': 'approval',
                'severity': 'danger',
                'title': '审批任务超时',
                'value': approval_overdue_count,
                'desc': f'待审批超过 {approval_pending_warn_hours} 小时',
            })
        if open_risk_count > 0:
            alerts.append({
                'source': 'risk',
                'severity': 'warning',
                'title': '待处理风险事件',
                'value': open_risk_count,
                'desc': '风险事件尚未闭环',
            })
        if latest_check and latest_check_issues > 0:
            alerts.append({
                'source': 'consistency',
                'severity': 'warning',
                'title': '一致性巡检存在问题',
                'value': latest_check_issues,
                'desc': f'最近巡检时间：{latest_check.created_at.strftime("%Y-%m-%d %H:%M")}',
            })
        if finance_mismatch_count > 0:
            alerts.append({
                'source': 'finance',
                'severity': 'warning',
                'title': '财务对账异常',
                'value': finance_mismatch_count,
                'desc': '最近巡检识别到财务差异订单',
            })

        if source_filter:
            alerts = [a for a in alerts if a.get('source') == source_filter]
        if severity_filter:
            alerts = [a for a in alerts if a.get('severity') == severity_filter]

        payload = {
            'summary': {
                'transfer_overdue_count': transfer_overdue_count,
                'approval_overdue_count': approval_overdue_count,
                'open_risk_count': open_risk_count,
                'latest_check_issues': latest_check_issues,
                'latest_check_type_counts': latest_check_type_counts if isinstance(latest_check_type_counts, dict) else {},
                'finance_mismatch_count': finance_mismatch_count,
                'alert_count': len(alerts),
            },
            'alerts': alerts,
            'filters': {
                'source': source_filter,
                'severity': severity_filter,
            },
            'executed_at': now.isoformat(),
        }

        notify_result = {'enabled': False, 'sent': 0, 'status': 'skipped'}
        if notify:
            notify_result = NotificationService.notify_alerts(
                title='运维中心告警',
                alerts=alerts,
                settings=settings,
                source='ops_watchdog',
            )
            payload['notify'] = notify_result

        if save_audit:
            AuditService.log_with_diff(
                user=None,
                action='status_change',
                module='运维中心',
                target='ops_watchdog',
                summary='执行运维告警聚合任务',
                before={},
                after=payload.get('summary') or {},
                extra={
                    'source': 'command',
                    'filters': payload.get('filters') or {},
                    'alerts': alerts,
                    'notify': notify_result,
                },
            )

        if as_json:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
            return

        self.stdout.write(
            f"[watchdog] alerts={len(alerts)} transfer_overdue={transfer_overdue_count} "
            f"approval_overdue={approval_overdue_count} open_risk={open_risk_count} "
            f"latest_check_issues={latest_check_issues}"
        )
        for item in alerts:
            self.stdout.write(
                f"- [{item.get('severity')}] {item.get('title')}: {item.get('value')} ({item.get('desc')})"
            )
