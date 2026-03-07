"""
核心业务模型
包含：用户扩展、订单、SKU、部件、采购、转寄、设置、日志等
"""
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from decimal import Decimal
from datetime import timedelta


class User(AbstractUser):
    """用户模型（扩展Django自带User）"""
    ROLE_CHOICES = [
        ('admin', '超级管理员'),
        ('manager', '业务经理'),
        ('warehouse_manager', '仓库主管'),
        ('warehouse_staff', '仓库操作员'),
        ('customer_service', '客服'),
    ]

    role = models.CharField('角色', max_length=20, choices=ROLE_CHOICES, default='warehouse_staff')
    phone = models.CharField('手机号', max_length=20, blank=True)
    full_name = models.CharField('真实姓名', max_length=50, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'users'
        verbose_name = '用户'
        verbose_name_plural = '用户'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


class SKU(models.Model):
    """SKU模型（租赁套装）"""
    code = models.CharField('SKU编码', max_length=50, unique=True)
    name = models.CharField('SKU名称', max_length=100)
    category = models.CharField('分类', max_length=50, default='主题套餐')
    image = models.FileField('SKU图片', upload_to='sku_images/', blank=True, null=True)
    rental_price = models.DecimalField('租金', max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    deposit = models.DecimalField('押金', max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    stock = models.IntegerField('总库存', validators=[MinValueValidator(0)])
    description = models.TextField('描述', blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'skus'
        verbose_name = 'SKU'
        verbose_name_plural = 'SKU'
        ordering = ['code']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_available_count(self, date):
        """获取仓库实时可用数量（date 参数保留兼容）"""
        # 查询当前未回仓的占用数量
        occupied = OrderItem.objects.filter(
            order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
            sku=self
        ).aggregate(total=models.Sum('quantity'))['total'] or 0

        return self.stock - occupied


class InventoryUnit(models.Model):
    """SKU单套库存实例（唯一编号追踪）"""
    STATUS_CHOICES = [
        ('in_warehouse', '在库'),
        ('in_transit', '在途'),
        ('maintenance', '维修中'),
        ('scrapped', '已报废'),
    ]

    LOCATION_CHOICES = [
        ('warehouse', '仓库'),
        ('order', '订单'),
        ('transit', '物流在途'),
        ('unknown', '未知'),
    ]

    sku = models.ForeignKey(SKU, on_delete=models.CASCADE, related_name='units', verbose_name='SKU')
    unit_no = models.CharField('单套编号', max_length=64, unique=True)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='in_warehouse')
    current_order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_units', verbose_name='当前归属订单')
    current_location_type = models.CharField('当前位置类型', max_length=20, choices=LOCATION_CHOICES, default='warehouse')
    last_tracking_no = models.CharField('最近物流单号', max_length=100, blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'inventory_units'
        verbose_name = '库存单套实例'
        verbose_name_plural = '库存单套实例'
        ordering = ['sku_id', 'unit_no']
        indexes = [
            models.Index(fields=['sku', 'status']),
            models.Index(fields=['current_order']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"{self.unit_no} ({self.sku.code})"


class UnitMovement(models.Model):
    """单套库存流转节点日志"""
    EVENT_CHOICES = [
        ('WAREHOUSE_OUT', '仓库发出'),
        ('TRANSFER_PENDING', '转寄待执行'),
        ('TRANSFER_SHIPPED', '转寄寄出'),
        ('TRANSFER_COMPLETED', '转寄完成'),
        ('RETURN_SHIPPED', '回仓在途'),
        ('RETURNED_WAREHOUSE', '已回仓'),
        ('EXCEPTION', '异常'),
    ]

    STATUS_CHOICES = [
        ('normal', '正常'),
        ('warning', '预警'),
        ('timeout', '超时'),
        ('closed', '闭环完成'),
    ]

    unit = models.ForeignKey(InventoryUnit, on_delete=models.CASCADE, related_name='movements', verbose_name='单套实例')
    event_type = models.CharField('节点类型', max_length=30, choices=EVENT_CHOICES)
    status = models.CharField('节点状态', max_length=20, choices=STATUS_CHOICES, default='normal')
    from_order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='unit_moves_from', verbose_name='来源订单')
    to_order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='unit_moves_to', verbose_name='目标订单')
    transfer = models.ForeignKey('Transfer', on_delete=models.SET_NULL, null=True, blank=True, related_name='unit_movements', verbose_name='关联转寄任务')
    tracking_no = models.CharField('物流单号', max_length=100, blank=True)
    notes = models.TextField('备注', blank=True)
    operator = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='操作人')
    event_time = models.DateTimeField('节点时间', auto_now_add=True)

    class Meta:
        db_table = 'unit_movements'
        verbose_name = '单套流转日志'
        verbose_name_plural = '单套流转日志'
        ordering = ['-event_time']
        indexes = [
            models.Index(fields=['event_type', 'status']),
            models.Index(fields=['unit', 'event_time']),
        ]

    def __str__(self):
        return f"{self.unit.unit_no} - {self.event_type}"


class Order(models.Model):
    """订单模型"""
    STATUS_CHOICES = [
        ('pending', '待处理'),
        ('confirmed', '待发货'),
        ('delivered', '已发货'),
        ('in_use', '使用中'),
        ('returned', '已归还'),
        ('completed', '已完成'),
        ('cancelled', '已取消'),
    ]

    order_no = models.CharField('订单号', max_length=50, unique=True)
    customer_name = models.CharField('客户姓名', max_length=100)
    customer_phone = models.CharField('联系电话', max_length=20)
    customer_email = models.EmailField('邮箱', blank=True)

    # 地址信息
    delivery_address = models.TextField('收货地址')
    return_address = models.TextField('回收地址', blank=True)

    # 日期信息
    event_date = models.DateField('预定日期')
    rental_days = models.IntegerField('租赁天数', default=1, validators=[MinValueValidator(1)])
    ship_date = models.DateField('发货日期', null=True, blank=True)
    return_date = models.DateField('回收日期', null=True, blank=True)

    # 物流信息
    ship_tracking = models.CharField('发货单号', max_length=100, blank=True)
    return_tracking = models.CharField('回收单号', max_length=100, blank=True)

    # 金额信息
    total_amount = models.DecimalField('订单总额', max_digits=10, decimal_places=2, default=Decimal('0.00'))
    deposit_paid = models.DecimalField('已付押金', max_digits=10, decimal_places=2, default=Decimal('0.00'))
    balance = models.DecimalField('待收尾款', max_digits=10, decimal_places=2, default=Decimal('0.00'))

    # 状态和备注
    status = models.CharField('订单状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField('备注', blank=True)

    # 创建和更新信息
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_orders', verbose_name='创建人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'orders'
        verbose_name = '订单'
        verbose_name_plural = '订单'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['order_no']),
            models.Index(fields=['status']),
            models.Index(fields=['event_date']),
            models.Index(fields=['customer_phone']),
        ]

    def __str__(self):
        return f"{self.order_no} - {self.customer_name}"

    def save(self, *args, **kwargs):
        """保存时自动计算日期和金额"""
        # 如果没有订单号，自动生成
        if not self.order_no:
            from django.utils import timezone
            base = timezone.now().strftime('%Y%m%d%H%M%S%f')
            self.order_no = f"ORD{base}"
            # 极端并发下兜底，确保唯一
            suffix = 1
            while Order.objects.filter(order_no=self.order_no).exists():
                self.order_no = f"ORD{base}{suffix}"
                suffix += 1

        # 计算发货日期和回收日期（如果没有设置）
        if not self.ship_date and self.event_date:
            from .utils import get_system_settings
            settings = get_system_settings()
            self.ship_date = self.event_date - timedelta(days=settings.get('ship_lead_days', 2))

        if not self.return_date and self.event_date:
            from .utils import get_system_settings
            settings = get_system_settings()
            self.return_date = self.event_date + timedelta(days=self.rental_days) + timedelta(days=settings.get('return_offset_days', 1))

        super().save(*args, **kwargs)


class OrderItem(models.Model):
    """订单明细"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items', verbose_name='订单')
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, verbose_name='SKU')
    quantity = models.IntegerField('数量', default=1, validators=[MinValueValidator(1)])
    rental_price = models.DecimalField('租金单价', max_digits=10, decimal_places=2)
    deposit = models.DecimalField('押金单价', max_digits=10, decimal_places=2)
    subtotal = models.DecimalField('小计', max_digits=10, decimal_places=2)

    class Meta:
        db_table = 'order_items'
        verbose_name = '订单明细'
        verbose_name_plural = '订单明细'

    def __str__(self):
        return f"{self.order.order_no} - {self.sku.name}"

    def save(self, *args, **kwargs):
        """保存时自动计算小计"""
        # 订单明细小计仅统计租金，押金单独管理
        self.subtotal = self.rental_price * self.quantity
        super().save(*args, **kwargs)


class Part(models.Model):
    """部件模型"""
    CATEGORY_CHOICES = [
        ('main', '主体道具'),
        ('accessory', '配件'),
        ('consumable', '消耗品'),
        ('packaging', '包装材料'),
        ('other', '其他'),
    ]

    name = models.CharField('部件名称', max_length=100)
    spec = models.CharField('规格型号', max_length=100, blank=True)
    category = models.CharField('分类', max_length=20, choices=CATEGORY_CHOICES, default='accessory')
    unit = models.CharField('单位', max_length=20, default='个')
    current_stock = models.IntegerField('当前库存', default=0)
    safety_stock = models.IntegerField('安全库存', default=0, validators=[MinValueValidator(0)])
    location = models.CharField('存放位置', max_length=100, blank=True)
    last_inbound_date = models.DateField('最近入库日期', null=True, blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'parts'
        verbose_name = '部件'
        verbose_name_plural = '部件'
        ordering = ['name']
        indexes = [
            models.Index(fields=['category']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"{self.name} ({self.spec})"

    @property
    def is_low_stock(self):
        """是否库存不足"""
        return self.current_stock < self.safety_stock


class PurchaseOrder(models.Model):
    """采购单模型"""
    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('ordered', '已下单'),
        ('arrived', '已到货'),
        ('stocked', '已入库'),
        ('cancelled', '已取消'),
    ]

    CHANNEL_CHOICES = [
        ('online', '网购平台'),
        ('secondhand', '闲鱼二手'),
        ('offline', '线下市场'),
        ('other', '其他'),
    ]

    po_no = models.CharField('采购单号', max_length=50, unique=True)
    channel = models.CharField('来源渠道', max_length=20, choices=CHANNEL_CHOICES)
    supplier = models.CharField('供应商/店铺', max_length=200)
    link = models.URLField('商品链接', blank=True)
    order_date = models.DateField('下单日期')
    arrival_date = models.DateField('到货日期', null=True, blank=True)
    total_amount = models.DecimalField('总金额', max_digits=10, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='draft')
    notes = models.TextField('备注', blank=True)

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_purchase_orders', verbose_name='创建人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'purchase_orders'
        verbose_name = '采购单'
        verbose_name_plural = '采购单'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['po_no']),
            models.Index(fields=['status']),
            models.Index(fields=['order_date']),
        ]

    def __str__(self):
        return f"{self.po_no} - {self.supplier}"

    def save(self, *args, **kwargs):
        """保存时自动生成采购单号"""
        if not self.po_no:
            from django.utils import timezone
            base = timezone.now().strftime('%Y%m%d%H%M%S%f')
            self.po_no = f"PO{base}"
            suffix = 1
            while PurchaseOrder.objects.filter(po_no=self.po_no).exists():
                self.po_no = f"PO{base}{suffix}"
                suffix += 1
        super().save(*args, **kwargs)


class PurchaseOrderItem(models.Model):
    """采购单明细"""
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items', verbose_name='采购单')
    part = models.ForeignKey(Part, on_delete=models.PROTECT, verbose_name='部件', null=True, blank=True)
    part_name = models.CharField('部件名称', max_length=100)
    spec = models.CharField('规格型号', max_length=100, blank=True)
    unit = models.CharField('单位', max_length=20, default='个')
    quantity = models.IntegerField('数量', validators=[MinValueValidator(1)])
    unit_price = models.DecimalField('单价', max_digits=10, decimal_places=2)
    subtotal = models.DecimalField('小计', max_digits=10, decimal_places=2)

    class Meta:
        db_table = 'purchase_order_items'
        verbose_name = '采购单明细'
        verbose_name_plural = '采购单明细'

    def __str__(self):
        return f"{self.purchase_order.po_no} - {self.part_name}"

    def save(self, *args, **kwargs):
        """保存时自动计算小计"""
        self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)


class PartsMovement(models.Model):
    """部件出入库流水"""
    TYPE_CHOICES = [
        ('inbound', '入库'),
        ('outbound', '出库'),
        ('adjustment', '调整'),
    ]

    part = models.ForeignKey(Part, on_delete=models.PROTECT, verbose_name='部件')
    type = models.CharField('类型', max_length=20, choices=TYPE_CHOICES)
    quantity = models.IntegerField('数量')
    related_doc = models.CharField('关联单据', max_length=100, blank=True)
    notes = models.TextField('备注', blank=True)

    operator = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='操作人')
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        db_table = 'parts_movements'
        verbose_name = '部件流水'
        verbose_name_plural = '部件流水'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['part', 'created_at']),
            models.Index(fields=['type']),
        ]

    def __str__(self):
        return f"{self.part.name} - {self.get_type_display()} {self.quantity}"

    def save(self, *args, **kwargs):
        """保存时自动更新部件库存"""
        is_new = self.pk is None
        super().save(*args, **kwargs)

        if is_new:
            # 更新部件库存
            if self.type == 'inbound':
                self.part.current_stock += self.quantity
            elif self.type == 'outbound':
                self.part.current_stock -= self.quantity
            elif self.type == 'adjustment':
                self.part.current_stock = self.quantity

            # 更新最近入库日期
            if self.type == 'inbound':
                from django.utils import timezone
                self.part.last_inbound_date = timezone.now().date()

            self.part.save()


class Transfer(models.Model):
    """转寄任务模型"""
    STATUS_CHOICES = [
        ('pending', '待执行'),
        ('completed', '已完成'),
        ('cancelled', '已取消'),
    ]

    order_from = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='transfers_from', verbose_name='回收订单')
    order_to = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='transfers_to', verbose_name='发货订单')
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, verbose_name='SKU')
    quantity = models.IntegerField('数量', default=1)
    gap_days = models.IntegerField('间隔天数')
    cost_saved = models.DecimalField('节省成本', max_digits=10, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField('备注', blank=True)

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='创建人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'transfers'
        verbose_name = '转寄任务'
        verbose_name_plural = '转寄任务'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.order_from.order_no} -> {self.order_to.order_no}"


class TransferAllocation(models.Model):
    """订单创建阶段的转寄分配锁"""
    STATUS_CHOICES = [
        ('locked', '已锁定'),
        ('released', '已释放'),
        ('consumed', '已消耗'),
    ]

    source_order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='transfer_allocations_source', verbose_name='来源订单')
    target_order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='transfer_allocations_target', verbose_name='目标订单')
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, verbose_name='SKU')
    quantity = models.IntegerField('分配数量', default=1, validators=[MinValueValidator(1)])
    target_event_date = models.DateField('目标预定日期')
    window_start = models.DateField('锁窗口开始')
    window_end = models.DateField('锁窗口结束')
    distance_score = models.DecimalField('地址距离分值', max_digits=8, decimal_places=4, default=Decimal('0.0000'))
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='locked')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='创建人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'transfer_allocations'
        verbose_name = '转寄分配锁'
        verbose_name_plural = '转寄分配锁'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['source_order', 'sku', 'status']),
            models.Index(fields=['target_order', 'status']),
            models.Index(fields=['target_event_date']),
        ]

    def __str__(self):
        return f"{self.source_order.order_no} -> {self.target_order.order_no} ({self.quantity})"


class SystemSettings(models.Model):
    """系统设置模型"""
    key = models.CharField('设置键', max_length=100, unique=True)
    value = models.CharField('设置值', max_length=500)
    description = models.CharField('描述', max_length=200, blank=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'system_settings'
        verbose_name = '系统设置'
        verbose_name_plural = '系统设置'

    def __str__(self):
        return f"{self.key} = {self.value}"


class AuditLog(models.Model):
    """操作日志模型"""
    ACTION_CHOICES = [
        ('create', '创建'),
        ('update', '修改'),
        ('delete', '删除'),
        ('status_change', '状态变更'),
        ('inbound', '入库'),
        ('outbound', '出库'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='操作人')
    action = models.CharField('操作类型', max_length=20, choices=ACTION_CHOICES)
    module = models.CharField('模块', max_length=50)
    target = models.CharField('操作对象', max_length=100)
    details = models.TextField('详细信息', blank=True)
    ip_address = models.GenericIPAddressField('IP地址', null=True, blank=True)
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        db_table = 'audit_logs'
        verbose_name = '操作日志'
        verbose_name_plural = '操作日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['module']),
            models.Index(fields=['action']),
        ]

    def __str__(self):
        return f"{self.user} - {self.get_action_display()} - {self.target}"

