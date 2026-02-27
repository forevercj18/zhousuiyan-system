"""
REST API 序列化器
"""
from rest_framework import serializers
from apps.core.models import (
    User, Order, OrderItem, SKU, Part, PurchaseOrder,
    PurchaseOrderItem, PartsMovement, Transfer, SystemSettings, AuditLog
)


class UserSerializer(serializers.ModelSerializer):
    """用户序列化器"""
    role_display = serializers.CharField(source='get_role_display', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'full_name', 'phone', 'role', 'role_display',
                  'is_active', 'last_login', 'created_at']
        read_only_fields = ['id', 'last_login', 'created_at']


class SKUSerializer(serializers.ModelSerializer):
    """SKU序列化器"""

    class Meta:
        model = SKU
        fields = ['id', 'code', 'name', 'category', 'rental_price', 'deposit',
                  'stock', 'description', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class OrderItemSerializer(serializers.ModelSerializer):
    """订单明细序列化器"""
    sku_name = serializers.CharField(source='sku.name', read_only=True)
    sku_code = serializers.CharField(source='sku.code', read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'sku', 'sku_name', 'sku_code', 'quantity',
                  'rental_price', 'deposit', 'subtotal']
        read_only_fields = ['id', 'subtotal']


class OrderSerializer(serializers.ModelSerializer):
    """订单序列化器"""
    items = OrderItemSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'order_no', 'customer_name', 'customer_phone', 'customer_email',
                  'delivery_address', 'return_address', 'event_date', 'rental_days',
                  'ship_date', 'return_date', 'ship_tracking', 'return_tracking',
                  'total_amount', 'deposit_paid', 'balance', 'status', 'status_display',
                  'notes', 'items', 'created_by', 'created_by_name', 'created_at', 'updated_at']
        read_only_fields = ['id', 'order_no', 'ship_date', 'return_date', 'total_amount',
                            'balance', 'created_at', 'updated_at']


class OrderCreateSerializer(serializers.Serializer):
    """创建订单序列化器"""
    customer_name = serializers.CharField(max_length=100)
    customer_phone = serializers.CharField(max_length=20)
    customer_email = serializers.EmailField(required=False, allow_blank=True)
    delivery_address = serializers.CharField()
    return_address = serializers.CharField(required=False, allow_blank=True)
    event_date = serializers.DateField()
    rental_days = serializers.IntegerField(default=1, min_value=1)
    notes = serializers.CharField(required=False, allow_blank=True)
    items = serializers.ListField(
        child=serializers.DictField(child=serializers.IntegerField())
    )


class PartSerializer(serializers.ModelSerializer):
    """部件序列化器"""
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Part
        fields = ['id', 'name', 'spec', 'category', 'category_display', 'unit',
                  'current_stock', 'safety_stock', 'location', 'last_inbound_date',
                  'is_active', 'is_low_stock', 'created_at', 'updated_at']
        read_only_fields = ['id', 'current_stock', 'last_inbound_date', 'created_at', 'updated_at']


class PartsMovementSerializer(serializers.ModelSerializer):
    """部件流水序列化器"""
    part_name = serializers.CharField(source='part.name', read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    operator_name = serializers.CharField(source='operator.full_name', read_only=True)

    class Meta:
        model = PartsMovement
        fields = ['id', 'part', 'part_name', 'type', 'type_display', 'quantity',
                  'related_doc', 'notes', 'operator', 'operator_name', 'created_at']
        read_only_fields = ['id', 'created_at']


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    """采购单明细序列化器"""

    class Meta:
        model = PurchaseOrderItem
        fields = ['id', 'part', 'part_name', 'spec', 'unit', 'quantity',
                  'unit_price', 'subtotal']
        read_only_fields = ['id', 'subtotal']


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """采购单序列化器"""
    items = PurchaseOrderItemSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    channel_display = serializers.CharField(source='get_channel_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ['id', 'po_no', 'channel', 'channel_display', 'supplier', 'link',
                  'order_date', 'arrival_date', 'total_amount', 'status', 'status_display',
                  'notes', 'items', 'created_by', 'created_by_name', 'created_at', 'updated_at']
        read_only_fields = ['id', 'po_no', 'total_amount', 'created_at', 'updated_at']


class TransferSerializer(serializers.ModelSerializer):
    """转寄任务序列化器"""
    order_from_no = serializers.CharField(source='order_from.order_no', read_only=True)
    order_to_no = serializers.CharField(source='order_to.order_no', read_only=True)
    sku_name = serializers.CharField(source='sku.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Transfer
        fields = ['id', 'order_from', 'order_from_no', 'order_to', 'order_to_no',
                  'sku', 'sku_name', 'quantity', 'gap_days', 'cost_saved',
                  'status', 'status_display', 'notes', 'created_at', 'updated_at']
        read_only_fields = ['id', 'gap_days', 'cost_saved', 'created_at', 'updated_at']


class SystemSettingsSerializer(serializers.ModelSerializer):
    """系统设置序列化器"""

    class Meta:
        model = SystemSettings
        fields = ['id', 'key', 'value', 'description', 'updated_at']
        read_only_fields = ['id', 'updated_at']


class AuditLogSerializer(serializers.ModelSerializer):
    """操作日志序列化器"""
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    action_display = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model = AuditLog
        fields = ['id', 'user', 'user_name', 'action', 'action_display', 'module',
                  'target', 'details', 'ip_address', 'created_at']
        read_only_fields = ['id', 'created_at']

