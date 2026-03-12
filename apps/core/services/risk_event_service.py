from django.utils import timezone

from ..models import RiskEvent


class RiskEventService:
    """风险事件服务（轻量工单）"""

    @staticmethod
    def create_event(*, event_type, level='medium', module='', title='', description='', event_data=None, order=None, transfer=None, detected_by=None):
        event_data = event_data or {}
        dup_qs = RiskEvent.objects.filter(
            event_type=event_type,
            status__in=['open', 'processing'],
        )
        if order is not None:
            dup_qs = dup_qs.filter(order=order)
        if transfer is not None:
            dup_qs = dup_qs.filter(transfer=transfer)
        duplicate = dup_qs.order_by('-created_at').first()
        if duplicate:
            return duplicate, False

        event = RiskEvent.objects.create(
            event_type=event_type,
            level=level,
            module=module,
            title=title,
            description=description,
            event_data=event_data,
            order=order,
            transfer=transfer,
            detected_by=detected_by,
        )
        return event, True

    @staticmethod
    def resolve_event(event, user, note=''):
        if event.status == 'closed':
            return event
        event.status = 'closed'
        event.resolved_by = user
        if not event.assignee_id:
            event.assignee = user
        event.resolved_at = timezone.now()
        if note:
            event.processing_note = (event.processing_note + '\n' if event.processing_note else '') + note
        event.save(update_fields=['status', 'resolved_by', 'assignee', 'resolved_at', 'processing_note', 'updated_at'])
        return event

    @staticmethod
    def claim_event(event, user, note=''):
        if event.status == 'closed':
            return event
        event.status = 'processing'
        event.assignee = user
        if note:
            event.processing_note = (event.processing_note + '\n' if event.processing_note else '') + note
        event.save(update_fields=['status', 'assignee', 'processing_note', 'updated_at'])
        return event
