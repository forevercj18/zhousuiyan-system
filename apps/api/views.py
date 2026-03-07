"""API视图模块 - RESTful API接口（真实数据库）"""
from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.models import Order, SKU, Part, PurchaseOrder, Transfer, TransferAllocation
from apps.core.permissions import filter_queryset_by_permission, has_action_permission
from apps.core.services import OrderService, ProcurementService, AuditService
from apps.core.utils import create_transfer_task, get_dashboard_stats_payload, get_role_dashboard_payload
from .serializers import OrderSerializer, SKUSerializer, PartSerializer, PurchaseOrderSerializer, TransferSerializer


def _is_transfer_source_order_active(order):
    return TransferAllocation.objects.filter(
        source_order=order,
        status__in=['locked', 'consumed'],
        target_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
    ).exists()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_orders_list(request):
    """获取订单列表"""
    queryset = Order.objects.select_related('created_by').prefetch_related('items__sku').order_by('-created_at')
    queryset = filter_queryset_by_permission(queryset, request.user, 'Order')

    status_filter = request.GET.get('status')
    if status_filter:
        queryset = queryset.filter(status=status_filter)

    serializer = OrderSerializer(queryset, many=True)
    return Response({
        'success': True,
        'data': serializer.data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_skus_list(request):
    """获取SKU列表"""
    queryset = SKU.objects.filter(is_active=True).order_by('code')
    serializer = SKUSerializer(queryset, many=True)
    return Response({
        'success': True,
        'data': serializer.data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_parts_inventory(request):
    """获取部件库存"""
    queryset = Part.objects.filter(is_active=True).order_by('name')
    category = request.GET.get('category')
    if category:
        queryset = queryset.filter(category=category)

    serializer = PartSerializer(queryset, many=True)
    return Response({
        'success': True,
        'data': serializer.data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_dashboard_stats(request):
    """获取工作台统计数据"""
    stats = get_dashboard_stats_payload(include_transfer_available=True)
    return Response({
        'success': True,
        'data': stats
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_dashboard_role_view(request):
    """获取角色看板V1数据（只读聚合）"""
    payload = get_role_dashboard_payload(request.user)
    return Response({
        'success': True,
        'data': payload
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_order_confirm(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    deposit_paid = Decimal(str(request.data.get('deposit_paid', '0') or '0'))
    try:
        order = OrderService.confirm_order(order.id, deposit_paid, request.user)
        return Response({'success': True, 'data': OrderSerializer(order).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_order_mark_delivered(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    try:
        if not has_action_permission(request.user, 'order.confirm_delivery'):
            raise ValueError('您没有执行此操作的权限（order.confirm_delivery）')
        order = OrderService.mark_as_delivered(order.id, request.data.get('ship_tracking', ''), request.user)
        return Response({'success': True, 'data': OrderSerializer(order).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_order_mark_returned(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    balance_paid = Decimal(str(request.data.get('balance_paid', '0') or '0'))
    try:
        if not has_action_permission(request.user, 'order.mark_returned'):
            raise ValueError('您没有执行此操作的权限（order.mark_returned）')
        if _is_transfer_source_order_active(order):
            raise ValueError('该订单为转寄链路订单，请前往【转寄中心】完成操作')
        order = OrderService.mark_as_returned(order.id, request.data.get('return_tracking', ''), balance_paid, request.user)
        return Response({'success': True, 'data': OrderSerializer(order).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_order_complete(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    try:
        order = OrderService.complete_order(order.id, request.user)
        return Response({'success': True, 'data': OrderSerializer(order).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_purchase_orders(request):
    queryset = PurchaseOrder.objects.select_related('created_by').prefetch_related('items').order_by('-created_at')
    serializer = PurchaseOrderSerializer(queryset, many=True)
    return Response({'success': True, 'data': serializer.data})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_purchase_order_mark_ordered(request, po_id):
    try:
        po = ProcurementService.mark_as_ordered(po_id, request.user)
        return Response({'success': True, 'data': PurchaseOrderSerializer(po).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_purchase_order_mark_arrived(request, po_id):
    try:
        po = ProcurementService.mark_as_arrived(po_id, request.user)
        return Response({'success': True, 'data': PurchaseOrderSerializer(po).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_purchase_order_mark_stocked(request, po_id):
    try:
        po = ProcurementService.mark_as_stocked(po_id, request.user)
        return Response({'success': True, 'data': PurchaseOrderSerializer(po).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_transfers(request):
    queryset = Transfer.objects.select_related('order_from', 'order_to', 'sku').order_by('-created_at')
    serializer = TransferSerializer(queryset, many=True)
    return Response({'success': True, 'data': serializer.data})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_transfer_create(request):
    try:
        if not has_action_permission(request.user, 'transfer.create_task'):
            raise ValueError('您没有执行此操作的权限（transfer.create_task）')
        order_from_id = int(request.data['order_from_id'])
        order_to_id = int(request.data['order_to_id'])
        sku_id = int(request.data['sku_id'])
        existed = Transfer.objects.filter(
            order_from_id=order_from_id,
            order_to_id=order_to_id,
            sku_id=sku_id,
            status='pending'
        ).exists()
        transfer = create_transfer_task(
            order_from_id,
            order_to_id,
            sku_id,
            request.user
        )
        AuditService.log_with_diff(
            user=request.user,
            action='create',
            module='转寄',
            target=f'任务#{transfer.id}',
            summary='API创建转寄任务' + ('（复用已有待执行任务）' if existed else ''),
            before={},
            after={
                'transfer_id': transfer.id,
                'order_from_id': transfer.order_from_id,
                'order_to_id': transfer.order_to_id,
                'sku_id': transfer.sku_id,
                'quantity': transfer.quantity,
                'status': transfer.status,
            },
            extra={'source': 'api', 'existing_pending_reused': existed},
        )
        return Response({'success': True, 'data': TransferSerializer(transfer).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)
