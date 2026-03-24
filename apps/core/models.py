"""
核心业务模型
包含：用户扩展、订单、SKU、部件、采购、转寄、设置、日志等
"""
from urllib.parse import quote

from django.conf import settings
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
    PERMISSION_MODE_CHOICES = [
        ('role', '固定角色'),
        ('custom', '自定义搭配'),
    ]

    role = models.CharField('角色', max_length=20, choices=ROLE_CHOICES, default='warehouse_staff')
    permission_mode = models.CharField('权限模式', max_length=20, choices=PERMISSION_MODE_CHOICES, default='role')
    custom_modules = models.JSONField('自定义模块权限', default=list, blank=True)
    custom_actions = models.JSONField('自定义操作权限', default=list, blank=True)
    custom_action_permissions = models.JSONField('自定义业务动作权限', default=list, blank=True)
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

    @property
    def permission_profile_display(self):
        if self.permission_mode == 'custom':
            return f'自定义搭配 / {self.get_role_display()}'
        return self.get_role_display()

    @property
    def role_display(self):
        return self.permission_profile_display

    @property
    def role_badge_class(self):
        if self.permission_mode == 'custom':
            return 'role-custom'
        return f'role-{self.role.replace("_", "-")}'


class PermissionTemplate(models.Model):
    """可复用权限模板"""
    name = models.CharField('模板名称', max_length=50, unique=True)
    base_role = models.CharField('基础角色', max_length=20, choices=User.ROLE_CHOICES, default='warehouse_staff')
    description = models.CharField('说明', max_length=200, blank=True)
    modules = models.JSONField('模块权限', default=list, blank=True)
    actions = models.JSONField('操作权限', default=list, blank=True)
    action_permissions = models.JSONField('业务动作权限', default=list, blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'permission_templates'
        verbose_name = '权限模板'
        verbose_name_plural = '权限模板'
        ordering = ['name']

    def __str__(self):
        return self.name


def _build_storage_public_url(key):
    cleaned_key = (key or '').strip().lstrip('/')
    if not cleaned_key:
        return ''
    domain = (getattr(settings, 'R2_PUBLIC_DOMAIN', '') or '').strip().rstrip('/')
    if not domain:
        return ''
    return f"{domain}/{quote(cleaned_key, safe='/~')}"


class SKU(models.Model):
    """SKU模型（租赁套装）"""
    code = models.CharField('SKU编码', max_length=50, unique=True)
    name = models.CharField('SKU名称', max_length=100)
    category = models.CharField('分类', max_length=50, default='主题套餐')
    image = models.FileField('SKU图片', upload_to='sku_images/', blank=True, null=True)
    image_key = models.CharField('七牛图片Key', max_length=255, blank=True, default='')
    rental_price = models.DecimalField('租金', max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    deposit = models.DecimalField('押金', max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    stock = models.IntegerField('总库存', validators=[MinValueValidator(0)])
    description = models.TextField('描述', blank=True)
    is_active = models.BooleanField('是否启用', default=True)

    # 小程序展示字段（与真实库存独立，纯营销用途）
    display_stock = models.IntegerField('展示库存', default=0,
        help_text='小程序展示用，手动设置，不影响真实库存')
    display_stock_warning = models.IntegerField('展示库存预警线', default=0,
        help_text='低于此值时小程序显示即将售罄')
    mp_visible = models.BooleanField('小程序可见', default=False,
        help_text='控制是否在小程序中展示')
    mp_sort_order = models.IntegerField('小程序排序', default=0,
        help_text='小程序产品列表排序，数字越小越靠前')

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
            models.Index(fields=['mp_visible', 'mp_sort_order']),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def image_url(self):
        cover_image = self.images.filter(is_cover=True).first()
        if cover_image and cover_image.image_url:
            return cover_image.image_url
        first_image = self.images.first()
        if first_image and first_image.image_url:
            return first_image.image_url
        if self.image_key:
            return _build_storage_public_url(self.image_key)
        if self.image:
            try:
                return self.image.url
            except ValueError:
                return ''
        return ''

    @property
    def image_display_url(self):
        return self.image_url

    @property
    def effective_stock(self):
        """
        统一库存口径：
        - 若已生成单套，则以激活且未报废的单套数量为准
        - 否则回退到旧 stock 字段，兼容历史测试数据和初始化数据
        """
        unit_qs = self.units.filter(is_active=True).exclude(status='scrapped')
        unit_count = unit_qs.count()
        if unit_count > 0:
            return unit_count
        return int(self.stock or 0)

    def get_available_count(self, date):
        """获取仓库实时可用数量（date 参数保留兼容）"""
        # 查询当前未回仓的占用数量
        occupied = OrderItem.objects.filter(
            order__status__in=['pending', 'confirmed', 'delivered', 'in_use'],
            sku=self
        ).aggregate(total=models.Sum('quantity'))['total'] or 0

        return self.effective_stock - occupied


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
    source_assembly_order = models.ForeignKey(
        'AssemblyOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_units',
        verbose_name='来源装配单'
    )
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
        ('MAINTENANCE_CREATED', '维修工单创建'),
        ('MAINTENANCE_COMPLETED', '维修完成'),
        ('MAINTENANCE_REVERSED', '维修工单冲销'),
        ('UNIT_DISASSEMBLED', '单套拆解'),
        ('UNIT_SCRAPPED', '单套报废'),
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
    ORDER_SOURCE_CHOICES = [
        ('wechat', '微信成交'),
        ('xianyu', '闲鱼'),
        ('xiaohongshu', '小红书'),
        ('miniprogram', '小程序'),
        ('other', '其他'),
    ]
    RETURN_SERVICE_TYPE_CHOICES = [
        ('none', '无'),
        ('customer_self_return', '客户自寄回'),
        ('platform_return_included', '包回邮服务'),
    ]
    RETURN_SERVICE_PAYMENT_STATUS_CHOICES = [
        ('unpaid', '未收款'),
        ('paid', '已收款'),
        ('refunded', '已退款'),
    ]
    RETURN_SERVICE_PAYMENT_CHANNEL_CHOICES = [
        ('xianyu', '闲鱼'),
        ('xiaohongshu', '小红书'),
        ('wechat', '微信'),
        ('offline', '线下'),
    ]
    RETURN_PICKUP_STATUS_CHOICES = [
        ('not_required', '无需叫件'),
        ('pending_schedule', '待安排取件'),
        ('scheduled', '已安排取件'),
        ('picked_up', '已上门取件'),
        ('completed', '已完成'),
        ('cancelled', '已取消'),
    ]

    order_no = models.CharField('订单号', max_length=50, unique=True)
    customer_name = models.CharField('客户姓名', max_length=100)
    customer_phone = models.CharField('联系电话', max_length=20)
    customer_wechat = models.CharField('微信号', max_length=100, blank=True)
    xianyu_order_no = models.CharField('闲鱼订单号', max_length=100, blank=True)
    order_source = models.CharField('订单来源', max_length=20, choices=ORDER_SOURCE_CHOICES, default='wechat')
    source_order_no = models.CharField('平台单号', max_length=100, blank=True)
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
    return_service_type = models.CharField('回寄服务类型', max_length=30, choices=RETURN_SERVICE_TYPE_CHOICES, default='none')
    return_service_fee = models.DecimalField('包回邮服务费', max_digits=10, decimal_places=2, default=Decimal('0.00'))
    return_service_payment_status = models.CharField(
        '包回邮收款状态',
        max_length=20,
        choices=RETURN_SERVICE_PAYMENT_STATUS_CHOICES,
        default='unpaid',
    )
    return_service_payment_channel = models.CharField(
        '包回邮收款渠道',
        max_length=20,
        choices=RETURN_SERVICE_PAYMENT_CHANNEL_CHOICES,
        blank=True,
    )
    return_service_payment_reference = models.CharField('包回邮支付参考号', max_length=100, blank=True)
    return_pickup_status = models.CharField(
        '包回邮叫件状态',
        max_length=20,
        choices=RETURN_PICKUP_STATUS_CHOICES,
        default='not_required',
    )

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
            models.Index(fields=['order_source']),
            models.Index(fields=['source_order_no']),
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


class Reservation(models.Model):
    """预定单模型"""
    STATUS_CHOICES = [
        ('pending_info', '待补信息'),
        ('ready_to_convert', '可转正式订单'),
        ('converted', '已转订单'),
        ('cancelled', '已取消'),
        ('refunded', '已退款'),
    ]

    reservation_no = models.CharField('预定单号', max_length=50, unique=True)
    customer_wechat = models.CharField('微信号', max_length=100)
    customer_name = models.CharField('客户姓名', max_length=100, blank=True)
    customer_phone = models.CharField('联系电话', max_length=20, blank=True)
    city = models.CharField('意向城市', max_length=100, blank=True)
    delivery_address = models.TextField('收货地址', blank=True)
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name='reservations', verbose_name='意向款式')
    quantity = models.IntegerField('数量', default=1, validators=[MinValueValidator(1)])
    event_date = models.DateField('预定日期')
    deposit_amount = models.DecimalField('订金金额', max_digits=10, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending_info')
    notes = models.TextField('备注', blank=True)
    converted_order = models.OneToOneField(
        'Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='source_reservation',
        verbose_name='关联正式订单',
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_reservations', verbose_name='创建人')
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='owned_reservations', verbose_name='当前负责人')
    wechat_customer = models.ForeignKey(
        'WechatCustomer', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reservations',
        verbose_name='小程序客户'
    )
    SOURCE_CHOICES = [
        ('manual', '客服录入'),
        ('miniprogram', '小程序'),
    ]
    source = models.CharField('来源渠道', max_length=20, choices=SOURCE_CHOICES, default='manual')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'reservations'
        verbose_name = '预定单'
        verbose_name_plural = '预定单'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['reservation_no']),
            models.Index(fields=['status']),
            models.Index(fields=['event_date']),
            models.Index(fields=['customer_wechat']),
            models.Index(fields=['owner', 'status']),
        ]

    def __str__(self):
        return f"{self.reservation_no} - {self.customer_wechat}"

    def save(self, *args, **kwargs):
        if not self.reservation_no:
            from django.utils import timezone
            base = timezone.now().strftime('%Y%m%d%H%M%S%f')
            self.reservation_no = f"RSV{base}"
            suffix = 1
            while Reservation.objects.filter(reservation_no=self.reservation_no).exists():
                self.reservation_no = f"RSV{base}{suffix}"
                suffix += 1
        if not self.owner_id and self.created_by_id:
            self.owner_id = self.created_by_id
        super().save(*args, **kwargs)

    @property
    def can_convert(self):
        return self.status in ['pending_info', 'ready_to_convert'] and self.converted_order_id is None

    @property
    def followup_lead_days(self):
        from .utils import get_system_settings
        try:
            return int(get_system_settings().get('reservation_followup_lead_days', 7) or 7)
        except Exception:
            return 7

    @property
    def followup_date(self):
        return self.event_date - timedelta(days=self.followup_lead_days) if self.event_date else None

    @property
    def contact_status_code(self):
        if self.status == 'converted':
            return 'converted'
        if self.status in ['cancelled', 'refunded']:
            return 'closed'
        if not self.followup_date:
            return 'unknown'
        from django.utils import timezone
        today = timezone.localdate()
        if self.followup_date < today:
            return 'overdue'
        if self.followup_date == today:
            return 'today'
        return 'pending'

    @property
    def contact_status_label(self):
        return {
            'converted': '已转单',
            'closed': '已关闭',
            'overdue': '已逾期未联系',
            'today': '今日需联系',
            'pending': '未到联系日',
            'unknown': '待计算',
        }.get(self.contact_status_code, '待计算')

    @property
    def fulfillment_stage_code(self):
        if not self.converted_order_id:
            return 'not_converted'
        order_status = self.converted_order.status
        if order_status in ['pending', 'confirmed']:
            return 'awaiting_shipment'
        if order_status in ['delivered', 'in_use', 'returned']:
            return 'in_fulfillment'
        if order_status == 'completed':
            return 'completed'
        if order_status == 'cancelled':
            return 'cancelled'
        return 'unknown'

    @property
    def fulfillment_stage_label(self):
        return {
            'not_converted': '未转单',
            'awaiting_shipment': '已转单待发货',
            'in_fulfillment': '已发货履约中',
            'completed': '已履约完成',
            'cancelled': '正式订单已取消',
            'unknown': '待跟进',
        }.get(self.fulfillment_stage_code, '待跟进')

    @property
    def converted_order_shipping_followup_code(self):
        if not self.converted_order_id:
            return 'none'
        order = self.converted_order
        shipped = bool(order.ship_tracking) or order.status in ['delivered', 'in_use', 'returned', 'completed']
        if shipped:
            return 'shipped'
        if order.status not in ['pending', 'confirmed']:
            return 'none'
        if not order.ship_date:
            return 'missing_ship_date'
        from django.utils import timezone
        today = timezone.localdate()
        if order.ship_date <= today:
            return 'overdue'
        return 'normal'

    @property
    def converted_order_shipping_followup_label(self):
        return {
            'none': '-',
            'shipped': '已发货',
            'missing_ship_date': '待补发货日期',
            'overdue': '待发货超时',
            'normal': '待发货',
        }.get(self.converted_order_shipping_followup_code, '-')

    @property
    def converted_order_balance_followup_label(self):
        if not self.converted_order_id:
            return '-'
        if self.converted_order.status == 'cancelled':
            return '正式订单已取消'
        if (self.converted_order.balance or Decimal('0.00')) > Decimal('0.00'):
            return f"待收尾款 ￥{self.converted_order.balance}"
        return '尾款已结清'


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


class SKUComponent(models.Model):
    """SKU部件组成（BOM）"""
    sku = models.ForeignKey(SKU, on_delete=models.CASCADE, related_name='components', verbose_name='SKU')
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='sku_components', verbose_name='部件')
    quantity_per_set = models.IntegerField('单套用量', default=1, validators=[MinValueValidator(1)])
    notes = models.CharField('备注', max_length=200, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'sku_components'
        verbose_name = 'SKU部件组成'
        verbose_name_plural = 'SKU部件组成'
        ordering = ['sku_id', 'part__name']
        constraints = [
            models.UniqueConstraint(fields=['sku', 'part'], name='uniq_sku_part_component')
        ]
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['part']),
        ]

    def __str__(self):
        return f"{self.sku.code} - {self.part.name} x {self.quantity_per_set}"


class InventoryUnitPart(models.Model):
    """单套库存对应部件状态快照"""
    STATUS_CHOICES = [
        ('normal', '正常'),
        ('missing', '缺件'),
        ('damaged', '损坏'),
        ('lost', '丢失'),
    ]

    unit = models.ForeignKey(InventoryUnit, on_delete=models.CASCADE, related_name='unit_parts', verbose_name='单套实例')
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='inventory_unit_parts', verbose_name='部件')
    expected_quantity = models.IntegerField('应有数量', default=1, validators=[MinValueValidator(1)])
    actual_quantity = models.IntegerField('实有数量', default=1, validators=[MinValueValidator(0)])
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='normal')
    notes = models.CharField('备注', max_length=200, blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    last_checked_at = models.DateTimeField('最近盘点时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'inventory_unit_parts'
        verbose_name = '单套部件状态'
        verbose_name_plural = '单套部件状态'
        ordering = ['unit_id', 'part__name']
        constraints = [
            models.UniqueConstraint(fields=['unit', 'part'], name='uniq_unit_part_status')
        ]
        indexes = [
            models.Index(fields=['unit', 'is_active']),
            models.Index(fields=['part']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.unit.unit_no} - {self.part.name} ({self.get_status_display()})"


class AssemblyOrder(models.Model):
    """SKU 装配单：通过部件装配新增套餐库存"""
    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('completed', '已完成'),
        ('reversed', '已冲销'),
        ('cancelled', '已取消'),
    ]

    assembly_no = models.CharField('装配单号', max_length=50, unique=True)
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name='assembly_orders', verbose_name='SKU')
    quantity = models.IntegerField('装配套数', validators=[MinValueValidator(1)])
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='completed')
    notes = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_assembly_orders', verbose_name='创建人')
    completed_at = models.DateTimeField('完成时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'assembly_orders'
        verbose_name = '装配单'
        verbose_name_plural = '装配单'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['assembly_no']),
            models.Index(fields=['sku', 'status']),
        ]

    def __str__(self):
        return f"{self.assembly_no} - {self.sku.code}"

    def save(self, *args, **kwargs):
        if not self.assembly_no:
            from django.utils import timezone
            base = timezone.now().strftime('%Y%m%d%H%M%S%f')
            self.assembly_no = f"ASM{base}"
            suffix = 1
            while AssemblyOrder.objects.filter(assembly_no=self.assembly_no).exists():
                self.assembly_no = f"ASM{base}{suffix}"
                suffix += 1
        super().save(*args, **kwargs)


class AssemblyOrderItem(models.Model):
    """装配单部件扣减明细"""
    assembly_order = models.ForeignKey(AssemblyOrder, on_delete=models.CASCADE, related_name='items', verbose_name='装配单')
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='assembly_order_items', verbose_name='部件')
    quantity_per_set = models.IntegerField('单套用量', validators=[MinValueValidator(1)])
    required_quantity = models.IntegerField('应扣数量', validators=[MinValueValidator(1)])
    deducted_quantity = models.IntegerField('实扣数量', validators=[MinValueValidator(1)])
    notes = models.CharField('备注', max_length=200, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'assembly_order_items'
        verbose_name = '装配单明细'
        verbose_name_plural = '装配单明细'
        ordering = ['assembly_order_id', 'part__name']

    def __str__(self):
        return f"{self.assembly_order.assembly_no} - {self.part.name}"


class MaintenanceWorkOrder(models.Model):
    """单套维修/换件工单"""
    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('completed', '已完成'),
        ('cancelled', '已取消'),
    ]

    work_order_no = models.CharField('工单号', max_length=50, unique=True)
    unit = models.ForeignKey(InventoryUnit, on_delete=models.PROTECT, related_name='maintenance_work_orders', verbose_name='单套')
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name='maintenance_work_orders', verbose_name='SKU')
    issue_desc = models.TextField('问题描述', blank=True)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='draft')
    notes = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_maintenance_orders', verbose_name='创建人')
    completed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='completed_maintenance_orders', verbose_name='完成人')
    completed_at = models.DateTimeField('完成时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'maintenance_work_orders'
        verbose_name = '维修工单'
        verbose_name_plural = '维修工单'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['work_order_no']),
            models.Index(fields=['status']),
            models.Index(fields=['unit']),
        ]

    def __str__(self):
        return f"{self.work_order_no} - {self.unit.unit_no}"

    def save(self, *args, **kwargs):
        if not self.work_order_no:
            from django.utils import timezone
            base = timezone.now().strftime('%Y%m%d%H%M%S%f')
            self.work_order_no = f"MWO{base}"
            suffix = 1
            while MaintenanceWorkOrder.objects.filter(work_order_no=self.work_order_no).exists():
                self.work_order_no = f"MWO{base}{suffix}"
                suffix += 1
        super().save(*args, **kwargs)


class MaintenanceWorkOrderItem(models.Model):
    """维修工单换件明细"""
    work_order = models.ForeignKey(MaintenanceWorkOrder, on_delete=models.CASCADE, related_name='items', verbose_name='维修工单')
    old_part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='maintenance_old_items', verbose_name='故障部件')
    new_part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='maintenance_new_items', verbose_name='替换部件')
    replace_quantity = models.IntegerField('更换数量', validators=[MinValueValidator(1)])
    notes = models.CharField('备注', max_length=200, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'maintenance_work_order_items'
        verbose_name = '维修工单明细'
        verbose_name_plural = '维修工单明细'
        ordering = ['work_order_id', 'id']

    def __str__(self):
        return f"{self.work_order.work_order_no} - {self.old_part.name}->{self.new_part.name}"


class UnitDisposalOrder(models.Model):
    """单套拆解/报废工单"""
    ACTION_CHOICES = [
        ('disassemble', '拆解回件'),
        ('scrap', '报废停用'),
    ]
    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('completed', '已完成'),
        ('cancelled', '已取消'),
    ]

    disposal_no = models.CharField('工单号', max_length=50, unique=True)
    action_type = models.CharField('动作类型', max_length=20, choices=ACTION_CHOICES)
    unit = models.ForeignKey(InventoryUnit, on_delete=models.PROTECT, related_name='disposal_orders', verbose_name='单套')
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name='disposal_orders', verbose_name='SKU')
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='completed')
    issue_desc = models.TextField('原因说明', blank=True)
    notes = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_disposal_orders', verbose_name='创建人')
    completed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='completed_disposal_orders', verbose_name='完成人')
    completed_at = models.DateTimeField('完成时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'unit_disposal_orders'
        verbose_name = '单套处置工单'
        verbose_name_plural = '单套处置工单'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['disposal_no']),
            models.Index(fields=['status']),
            models.Index(fields=['action_type', 'status']),
            models.Index(fields=['unit']),
        ]

    def __str__(self):
        return f"{self.disposal_no} - {self.unit.unit_no}"

    def save(self, *args, **kwargs):
        if not self.disposal_no:
            from django.utils import timezone
            base = timezone.now().strftime('%Y%m%d%H%M%S%f')
            self.disposal_no = f"UDO{base}"
            suffix = 1
            while UnitDisposalOrder.objects.filter(disposal_no=self.disposal_no).exists():
                self.disposal_no = f"UDO{base}{suffix}"
                suffix += 1
        super().save(*args, **kwargs)


class UnitDisposalOrderItem(models.Model):
    """单套处置工单部件明细"""
    disposal_order = models.ForeignKey(UnitDisposalOrder, on_delete=models.CASCADE, related_name='items', verbose_name='处置工单')
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='unit_disposal_items', verbose_name='部件')
    quantity = models.IntegerField('数量', validators=[MinValueValidator(0)])
    returned_quantity = models.IntegerField('回收入库数量', validators=[MinValueValidator(0)], default=0)
    notes = models.CharField('备注', max_length=200, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'unit_disposal_order_items'
        verbose_name = '单套处置明细'
        verbose_name_plural = '单套处置明细'
        ordering = ['disposal_order_id', 'part__name']

    def __str__(self):
        return f"{self.disposal_order.disposal_no} - {self.part.name}"


class PartRecoveryInspection(models.Model):
    """拆解回件质检记录"""
    STATUS_CHOICES = [
        ('pending', '待质检'),
        ('returned', '已回库'),
        ('repair', '待维修'),
        ('scrapped', '已报废'),
    ]

    disposal_order = models.ForeignKey(UnitDisposalOrder, on_delete=models.CASCADE, related_name='recovery_inspections', verbose_name='来源处置单')
    disposal_item = models.ForeignKey(UnitDisposalOrderItem, on_delete=models.CASCADE, related_name='recovery_inspections', verbose_name='来源处置明细')
    unit = models.ForeignKey(InventoryUnit, on_delete=models.PROTECT, related_name='recovery_inspections', verbose_name='来源单套')
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name='part_recovery_inspections', verbose_name='SKU')
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='recovery_inspections', verbose_name='部件')
    quantity = models.IntegerField('待质检数量', validators=[MinValueValidator(1)])
    status = models.CharField('处理状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.CharField('备注', max_length=200, blank=True)
    processed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_part_recovery_inspections', verbose_name='处理人')
    processed_at = models.DateTimeField('处理时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'part_recovery_inspections'
        verbose_name = '拆解回件质检记录'
        verbose_name_plural = '拆解回件质检记录'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['part', 'status']),
            models.Index(fields=['unit']),
        ]

    def __str__(self):
        return f"{self.disposal_order.disposal_no} - {self.part.name} x {self.quantity}"


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


class FinanceTransaction(models.Model):
    """订单资金流水"""
    TYPE_CHOICES = [
        ('reservation_deposit_received', '收预定订金'),
        ('reservation_deposit_refund', '退预定订金'),
        ('reservation_deposit_applied', '预定订金转押金'),
        ('deposit_received', '收押金'),
        ('balance_received', '收尾款'),
        ('deposit_refund', '退押金'),
        ('return_service_received', '收包回邮服务费'),
        ('return_service_refund', '退包回邮服务费'),
        ('penalty_charge', '扣罚'),
        ('manual_adjust', '人工调整'),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, null=True, blank=True, related_name='finance_transactions', verbose_name='订单')
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, null=True, blank=True, related_name='finance_transactions', verbose_name='预定单')
    transaction_type = models.CharField('交易类型', max_length=30, choices=TYPE_CHOICES)
    amount = models.DecimalField('金额', max_digits=10, decimal_places=2, default=Decimal('0.00'))
    reference_no = models.CharField('关联单号', max_length=100, blank=True)
    notes = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='操作人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'finance_transactions'
        verbose_name = '资金流水'
        verbose_name_plural = '资金流水'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['order', 'transaction_type']),
            models.Index(fields=['reservation', 'transaction_type']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        subject = self.order.order_no if self.order else (self.reservation.reservation_no if self.reservation else '-')
        return f"{subject} {self.transaction_type} {self.amount}"

    @property
    def subject_no(self):
        if self.order:
            return self.order.order_no
        if self.reservation:
            return self.reservation.reservation_no
        return ''

    @property
    def subject_customer_name(self):
        if self.order:
            return self.order.customer_name
        if self.reservation:
            return self.reservation.customer_name or self.reservation.customer_wechat
        return ''

    @property
    def subject_type_label(self):
        if self.order:
            return '订单'
        if self.reservation:
            return '预定单'
        return '-'


class RiskEvent(models.Model):
    """风险事件（轻量工单）"""
    TYPE_CHOICES = [
        ('delivered_recommend_change', '已发货改单挂靠'),
        ('delivered_order_cancel', '已发货订单取消'),
        ('frequent_cancel', '高频取消'),
    ]
    LEVEL_CHOICES = [
        ('low', '低'),
        ('medium', '中'),
        ('high', '高'),
        ('critical', '严重'),
    ]
    STATUS_CHOICES = [
        ('open', '待处理'),
        ('processing', '处理中'),
        ('closed', '已关闭'),
    ]

    event_type = models.CharField('事件类型', max_length=40, choices=TYPE_CHOICES)
    level = models.CharField('风险级别', max_length=20, choices=LEVEL_CHOICES, default='medium')
    status = models.CharField('处理状态', max_length=20, choices=STATUS_CHOICES, default='open')
    module = models.CharField('模块', max_length=50, blank=True, default='')
    title = models.CharField('标题', max_length=200)
    description = models.TextField('描述', blank=True)
    event_data = models.JSONField('事件数据', default=dict, blank=True)

    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='risk_events', verbose_name='关联订单')
    transfer = models.ForeignKey(Transfer, on_delete=models.SET_NULL, null=True, blank=True, related_name='risk_events', verbose_name='关联转寄任务')
    detected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='risk_events_detected', verbose_name='触发人')
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='risk_events_assigned', verbose_name='负责人')
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='risk_events_resolved', verbose_name='处理人')
    processing_note = models.TextField('处理备注', blank=True)
    resolved_at = models.DateTimeField('处理时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'risk_events'
        verbose_name = '风险事件'
        verbose_name_plural = '风险事件'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['status', 'level']),
            models.Index(fields=['event_type', 'created_at']),
            models.Index(fields=['order', 'status']),
        ]

    def __str__(self):
        return f"[{self.level}] {self.title}"


class ApprovalTask(models.Model):
    """高风险动作审批任务"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('executed', '已执行'),
        ('rejected', '已驳回'),
    ]

    task_no = models.CharField('审批单号', max_length=50, unique=True)
    action_code = models.CharField('动作编码', max_length=50)
    module = models.CharField('模块', max_length=50, blank=True, default='')
    target_type = models.CharField('目标类型', max_length=50)
    target_id = models.IntegerField('目标ID')
    target_label = models.CharField('目标标识', max_length=120, blank=True, default='')
    summary = models.CharField('摘要', max_length=200)
    payload = models.JSONField('审批参数', default=dict, blank=True)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    required_review_count = models.IntegerField('所需审批人数', default=1)
    current_review_count = models.IntegerField('已审批人数', default=0)
    reviewed_user_ids = models.JSONField('已审批用户ID列表', default=list, blank=True)
    review_trail = models.JSONField('审批轨迹', default=list, blank=True)
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='approval_tasks_requested', verbose_name='申请人')
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approval_tasks_reviewed', verbose_name='审批人')
    review_note = models.CharField('审批备注', max_length=300, blank=True, default='')
    reviewed_at = models.DateTimeField('审批时间', null=True, blank=True)
    executed_at = models.DateTimeField('执行时间', null=True, blank=True)
    remind_count = models.IntegerField('催办次数', default=0)
    last_reminded_at = models.DateTimeField('最近催办时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'approval_tasks'
        verbose_name = '审批任务'
        verbose_name_plural = '审批任务'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['status', 'action_code']),
            models.Index(fields=['target_type', 'target_id']),
            models.Index(fields=['requested_by', 'status']),
        ]

    def __str__(self):
        return f"{self.task_no} {self.action_code} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.task_no:
            from django.utils import timezone
            base = timezone.now().strftime('%Y%m%d%H%M%S%f')
            self.task_no = f"APR{base}"
            suffix = 1
            while ApprovalTask.objects.filter(task_no=self.task_no).exists():
                self.task_no = f"APR{base}{suffix}"
                suffix += 1
        super().save(*args, **kwargs)


class DataConsistencyCheckRun(models.Model):
    """数据一致性巡检执行记录"""
    source = models.CharField('来源', max_length=30, default='manual')
    total_issues = models.IntegerField('问题总数', default=0)
    summary = models.JSONField('汇总信息', default=dict, blank=True)
    issues = models.JSONField('问题明细', default=list, blank=True)
    executed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='consistency_check_runs', verbose_name='执行人')
    created_at = models.DateTimeField('执行时间', auto_now_add=True)

    class Meta:
        db_table = 'data_consistency_check_runs'
        verbose_name = '一致性巡检记录'
        verbose_name_plural = '一致性巡检记录'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['source']),
        ]

    def __str__(self):
        return f"巡检#{self.id} ({self.total_issues})"


class TransferRecommendationLog(models.Model):
    """转寄推荐决策回放日志"""
    TRIGGER_CHOICES = [
        ('recommend', '转寄中心重推'),
        ('create', '创建订单自动推荐'),
        ('manual', '手工触发'),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='transfer_recommendation_logs', verbose_name='目标订单')
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name='transfer_recommendation_logs', verbose_name='SKU')
    trigger_type = models.CharField('触发类型', max_length=20, choices=TRIGGER_CHOICES, default='recommend')
    target_event_date = models.DateField('目标预定日期')
    target_address = models.TextField('目标地址', blank=True)
    before_source_order_ids = models.JSONField('推荐前来源单ID集合', default=list, blank=True)
    selected_source_order_id = models.IntegerField('推荐后来源单ID', null=True, blank=True)
    selected_source_order_no = models.CharField('推荐后来源单号', max_length=60, blank=True, default='')
    warehouse_needed = models.IntegerField('需仓库补量', default=0)
    candidates = models.JSONField('候选快照', default=list, blank=True)
    score_summary = models.JSONField('评分摘要', default=dict, blank=True)
    operator = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='transfer_recommendation_logs', verbose_name='触发人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'transfer_recommendation_logs'
        verbose_name = '转寄推荐日志'
        verbose_name_plural = '转寄推荐日志'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['order', 'sku', 'created_at']),
            models.Index(fields=['trigger_type', 'created_at']),
        ]

    def __str__(self):
        return f"{self.order.order_no} / {self.sku.code} / {self.trigger_type}"


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


class WechatCustomer(models.Model):
    """微信小程序客户"""
    openid = models.CharField('微信OpenID', max_length=128, unique=True, db_index=True)
    unionid = models.CharField('微信UnionID', max_length=128, blank=True, db_index=True)
    nickname = models.CharField('昵称', max_length=100, blank=True)
    avatar_url = models.URLField('头像URL', blank=True)
    phone = models.CharField('手机号', max_length=20, blank=True)
    wechat_id = models.CharField('微信号', max_length=100, blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('首次访问', auto_now_add=True)
    updated_at = models.DateTimeField('最近访问', auto_now=True)

    class Meta:
        db_table = 'wechat_customers'
        verbose_name = '微信客户'
        verbose_name_plural = '微信客户'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['openid']),
            models.Index(fields=['phone']),
        ]

    def __str__(self):
        return f"{self.nickname or self.openid}"


class WechatStaffBinding(models.Model):
    """微信小程序员工绑定关系"""
    customer = models.OneToOneField(
        WechatCustomer,
        on_delete=models.CASCADE,
        related_name='staff_binding',
        verbose_name='微信客户身份',
    )
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='wechat_staff_binding',
        verbose_name='后台用户',
    )
    is_active = models.BooleanField('是否启用', default=True)
    bound_at = models.DateTimeField('绑定时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wechat_staff_bindings'
        verbose_name = '微信员工绑定'
        verbose_name_plural = '微信员工绑定'

    def __str__(self):
        return f"{self.customer} -> {self.user}"


class SKUImage(models.Model):
    """SKU展示图片（多图）"""
    sku = models.ForeignKey(SKU, on_delete=models.CASCADE, related_name='images', verbose_name='SKU')
    image = models.FileField('图片', upload_to='sku_images/', blank=True, null=True)
    image_key = models.CharField('七牛图片Key', max_length=255, blank=True, default='')
    sort_order = models.IntegerField('排序', default=0)
    is_cover = models.BooleanField('是否封面', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'sku_images'
        verbose_name = 'SKU展示图片'
        verbose_name_plural = 'SKU展示图片'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f"{self.sku.code} - 图{self.sort_order}"

    @property
    def image_url(self):
        if self.image_key:
            return _build_storage_public_url(self.image_key)
        if self.image:
            try:
                return self.image.url
            except ValueError:
                return ''
        return ''
