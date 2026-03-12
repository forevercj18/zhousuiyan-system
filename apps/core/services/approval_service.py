from django.db import transaction
from django.utils import timezone

from apps.core.models import ApprovalTask


class ApprovalService:
    """审批任务服务"""

    @staticmethod
    @transaction.atomic
    def create_or_get_pending(
        *,
        action_code,
        module,
        target_type,
        target_id,
        target_label,
        summary,
        payload,
        requested_by,
        required_review_count=1,
    ):
        exists = ApprovalTask.objects.select_for_update().filter(
            action_code=action_code,
            target_type=target_type,
            target_id=target_id,
            status='pending',
        ).first()
        if exists:
            return exists, False

        task = ApprovalTask.objects.create(
            action_code=action_code,
            module=module or '',
            target_type=target_type,
            target_id=target_id,
            target_label=target_label or '',
            summary=summary,
            payload=payload or {},
            required_review_count=max(int(required_review_count or 1), 1),
            requested_by=requested_by,
        )
        return task, True

    @staticmethod
    @transaction.atomic
    def remind_pending_task(task_id):
        """审批催办：仅待审批任务可催办，记录催办次数与时间。"""
        task = ApprovalTask.objects.select_for_update().filter(id=task_id).first()
        if not task:
            raise ValueError('审批任务不存在')
        if task.status != 'pending':
            raise ValueError('仅待审批任务可催办')
        task.remind_count = int(task.remind_count or 0) + 1
        task.last_reminded_at = timezone.now()
        task.save(update_fields=['remind_count', 'last_reminded_at', 'updated_at'])
        return task
