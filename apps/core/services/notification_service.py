import json
import urllib.request
from datetime import date, datetime
from decimal import Decimal

from .audit_service import _to_serializable
from .audit_service import AuditService


def _severity_rank(level):
    mapping = {
        'info': 1,
        'warning': 2,
        'danger': 3,
        'critical': 4,
    }
    return mapping.get((level or '').strip().lower(), 1)


class NotificationService:
    """告警通知服务（Webhook + 审计兜底）"""

    @staticmethod
    def should_notify(*, enabled, alert_severity, min_severity):
        if not enabled:
            return False
        return _severity_rank(alert_severity) >= _severity_rank(min_severity or 'warning')

    @staticmethod
    def send_webhook(*, webhook_url, payload):
        if not webhook_url:
            raise ValueError('未配置Webhook地址')
        data = json.dumps(_to_serializable(payload), ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(getattr(resp, 'status', 200))

    @staticmethod
    def notify_alerts(*, title, alerts, settings, source='system'):
        """
        发送告警通知：
        - 命中阈值后按 webhook 推送
        - 无论成功/失败写审计日志
        """
        enabled = str(settings.get('alert_notify_enabled', '0')) in ['1', 'true', 'True']
        webhook_url = (settings.get('alert_notify_webhook_url') or '').strip()
        min_severity = (settings.get('alert_notify_min_severity') or 'warning').strip().lower()

        selected = [a for a in (alerts or []) if NotificationService.should_notify(
            enabled=enabled,
            alert_severity=a.get('severity'),
            min_severity=min_severity
        )]
        if not selected:
            return {'enabled': enabled, 'sent': 0, 'status': 'skipped'}

        payload = {
            'title': title,
            'source': source,
            'time': datetime.now().isoformat(),
            'count': len(selected),
            'alerts': selected,
        }
        send_status = 'skipped'
        error_msg = ''
        http_status = None
        if enabled and webhook_url:
            try:
                http_status = NotificationService.send_webhook(webhook_url=webhook_url, payload=payload)
                send_status = 'success'
            except Exception as e:
                send_status = 'failed'
                error_msg = str(e)
        else:
            send_status = 'skipped'

        AuditService.log_with_diff(
            user=None,
            action='status_change',
            module='通知中心',
            target=title,
            summary='发送告警通知',
            before={},
            after={
                'enabled': enabled,
                'status': send_status,
                'sent_count': len(selected),
                'http_status': http_status or 0,
            },
            extra={
                'source': source,
                'min_severity': min_severity,
                'error': error_msg,
                'alerts': selected,
            },
        )
        return {
            'enabled': enabled,
            'sent': len(selected),
            'status': send_status,
            'http_status': http_status,
            'error': error_msg,
        }
