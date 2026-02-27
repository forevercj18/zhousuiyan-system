"""API视图模块 - RESTful API接口（真实数据库）"""
from django.db.models import F
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.models import Order, SKU, Part
from apps.core.permissions import filter_queryset_by_permission
from .serializers import OrderSerializer, SKUSerializer, PartSerializer


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
    stats = {
        'pending_orders': Order.objects.filter(status='pending').count(),
        'confirmed_orders': Order.objects.filter(status='confirmed').count(),
        'delivered_orders': Order.objects.filter(status='delivered').count(),
        'total_orders': Order.objects.count(),
        'total_skus': SKU.objects.filter(is_active=True).count(),
        'low_stock_parts': Part.objects.filter(current_stock__lt=F('safety_stock')).count(),
    }
    return Response({
        'success': True,
        'data': stats
    })
