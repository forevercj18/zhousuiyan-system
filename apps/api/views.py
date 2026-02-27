"""API视图模块 - RESTful API接口（真实数据库）"""
from decimal import Decimal

from django.db.models import F, Sum
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.models import Order, SKU, Part, PurchaseOrder, Transfer, OrderItem, TransferAllocation
from apps.core.permissions import filter_queryset_by_permission
from apps.core.services import OrderService, ProcurementService
from apps.core.utils import create_transfer_task
from .serializers import OrderSerializer, SKUSerializer, PartSerializer, PurchaseOrderSerializer, TransferSerializer


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
    total_stock = SKU.objects.filter(is_active=True).aggregate(total=Sum('stock'))['total'] or 0
    occupied_raw = OrderItem.objects.filter(
        order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
        sku__is_active=True
    ).aggregate(total=Sum('quantity'))['total'] or 0
    transfer_allocated = TransferAllocation.objects.filter(
        target_order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
        sku__is_active=True,
        status__in=['locked', 'consumed']
    ).aggregate(total=Sum('quantity'))['total'] or 0
    occupied_stock = max(occupied_raw - transfer_allocated, 0)
    warehouse_available_stock = max(total_stock - occupied_stock, 0)
    total_revenue = Order.objects.filter(status='completed').aggregate(total=Sum('total_amount'))['total'] or 0
    pending_revenue = Order.objects.exclude(status__in=['completed', 'cancelled']).aggregate(total=Sum('balance'))['total'] or 0
    from apps.core.utils import find_transfer_candidates

    stats = {
        'pending_orders': Order.objects.filter(status='pending').count(),
        'delivered_orders': Order.objects.filter(status='delivered').count(),
        'warehouse_available_stock': warehouse_available_stock,
        'transfer_available_count': len(find_transfer_candidates()),
        'total_orders': Order.objects.count(),
        'total_skus': SKU.objects.filter(is_active=True).count(),
        'low_stock_parts': Part.objects.filter(current_stock__lt=F('safety_stock')).count(),
        'total_revenue': total_revenue,
        'pending_revenue': pending_revenue,
    }
    return Response({
        'success': True,
        'data': stats
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
        transfer = create_transfer_task(
            int(request.data['order_from_id']),
            int(request.data['order_to_id']),
            int(request.data['sku_id']),
            request.user
        )
        return Response({'success': True, 'data': TransferSerializer(transfer).data})
    except Exception as e:
        return Response({'success': False, 'message': str(e)}, status=400)
