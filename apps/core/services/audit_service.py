"""
审计日志服务：统一记录关键操作的 before/after 差异。
"""
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from ..models import AuditLog


def _to_serializable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _to_serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_serializable(v) for v in value]
    return value


def _build_changed_fields(before: Dict[str, Any], after: Dict[str, Any]) -> list[str]:
    keys = sorted(set(before.keys()) | set(after.keys()))
    changed = []
    for key in keys:
        if before.get(key) != after.get(key):
            changed.append(key)
    return changed


class AuditService:
    """统一审计入口。"""

    @staticmethod
    def log_with_diff(
        *,
        user,
        action: str,
        module: str,
        target: str,
        summary: str,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
        ip_address=None,
    ) -> AuditLog:
        before_data = _to_serializable(before or {})
        after_data = _to_serializable(after or {})
        extra_data = _to_serializable(extra or {})
        if isinstance(extra_data, dict) and not extra_data.get('source'):
            extra_data['source'] = 'app'
        payload = {
            'summary': summary,
            'before': before_data,
            'after': after_data,
            'changed_fields': _build_changed_fields(before_data, after_data),
            'extra': extra_data,
        }
        return AuditLog.objects.create(
            user=user,
            action=action,
            module=module,
            target=target,
            details=json.dumps(payload, ensure_ascii=False),
            ip_address=ip_address,
        )
