# 微信小程序——后端开发文档

**项目名称**：宝宝周岁宴道具租赁系统 - 微信小程序后端支撑
**版本**：v1.0
**日期**：2026-03-22
**前置依赖**：[设计文档](MINIPROGRAM_DESIGN_20260322.md)

---

## 一、开发任务分解

### 阶段总览

| 阶段 | 任务 | 预估工作量 | 依赖 |
|---|---|---|---|
| **P1** | 模型层改动 + 数据库迁移 | 小 | 无 |
| **P2** | 微信登录认证 | 中 | P1 |
| **P3** | 小程序 API 接口开发 | 中 | P1, P2 |
| **P4** | 后台 SKU 管理页面扩展 | 小 | P1 |
| **P5** | 测试 + 联调 | 中 | P1-P4 |
| **P6** | 小程序前端开发 | 主要工作量 | P3 |

---

## 二、P1 — 模型层改动

### 2.1 新增模型文件位置

所有模型定义仍放在 `apps/core/models.py` 中（遵循现有单文件模型架构）。

### 2.2 新增 `WechatCustomer` 模型

```python
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
```

### 2.3 新增 `SKUImage` 模型

```python
class SKUImage(models.Model):
    """SKU展示图片（多图）"""
    sku = models.ForeignKey(SKU, on_delete=models.CASCADE, related_name='images', verbose_name='SKU')
    image = models.FileField('图片', upload_to='sku_images/')
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
```

### 2.4 SKU 模型新增字段

```python
# 在 SKU 模型中新增以下字段
display_stock = models.IntegerField('展示库存', default=0,
    help_text='小程序展示用，手动设置，不影响真实库存')
display_stock_warning = models.IntegerField('展示库存预警线', default=0,
    help_text='低于此值时小程序显示即将售罄')
mp_visible = models.BooleanField('小程序可见', default=False,
    help_text='控制是否在小程序中展示')
mp_sort_order = models.IntegerField('小程序排序', default=0,
    help_text='小程序产品列表排序，数字越小越靠前')
```

**注意**：这些字段与现有库存字段 `stock` / `effective_stock` 完全无关，不影响任何现有库存计算逻辑。

### 2.5 Reservation 模型新增字段

```python
# 在 Reservation 模型中新增以下字段
wechat_customer = models.ForeignKey(
    'WechatCustomer', on_delete=models.SET_NULL,
    null=True, blank=True,
    related_name='reservations',
    verbose_name='小程序客户'
)
source = models.CharField('来源渠道', max_length=20, default='manual',
    choices=[('manual', '客服录入'), ('miniprogram', '小程序')])
```

### 2.6 Order 来源枚举扩展

```python
# Order.ORDER_SOURCE_CHOICES 追加
('miniprogram', '小程序'),
```

### 2.7 数据库迁移

```bash
python manage.py makemigrations core
# 预期迁移名：core.0025_miniprogram_models
python manage.py migrate
python manage.py check
```

---

## 三、P2 — 微信登录认证

### 3.1 微信小程序配置

需要在系统设置 `SystemSettings` 中新增两个配置项：

| key | 说明 | 示例值 |
|---|---|---|
| `mp_appid` | 微信小程序 AppID | `wx1234567890abcdef` |
| `mp_secret` | 微信小程序 AppSecret | `abc123...` |

也可通过环境变量 `MP_APPID` / `MP_SECRET` 注入，优先级：环境变量 > SystemSettings。

### 3.2 认证服务

新建 `apps/core/services/wechat_auth_service.py`：

```python
"""微信小程序认证服务"""
import hashlib
import hmac
import json
import time
import urllib.request
from django.conf import settings


def code_to_session(code):
    """
    调用微信 code2Session 接口获取 openid
    参考：https://developers.weixin.qq.com/miniprogram/dev/OpenApiDoc/user-login/code2Session.html
    """
    appid = _get_config('MP_APPID', 'mp_appid')
    secret = _get_config('MP_SECRET', 'mp_secret')

    url = (
        f"https://api.weixin.qq.com/sns/jscode2session"
        f"?appid={appid}&secret={secret}&js_code={code}"
        f"&grant_type=authorization_code"
    )
    # 使用 urllib 调用，不引入 requests 依赖
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read())

    if 'errcode' in data and data['errcode'] != 0:
        raise ValueError(f"微信登录失败: {data.get('errmsg', '未知错误')}")

    return {
        'openid': data['openid'],
        'session_key': data.get('session_key', ''),
        'unionid': data.get('unionid', ''),
    }


def generate_token(customer_id, openid):
    """
    生成自定义 Token（HMAC 签名，含过期时间）
    格式：{customer_id}.{expire_ts}.{signature}
    """
    expire_ts = int(time.time()) + 86400 * 7  # 7天过期
    payload = f"{customer_id}.{expire_ts}"
    secret_key = settings.SECRET_KEY
    signature = hmac.new(
        secret_key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{payload}.{signature}"


def verify_token(token):
    """
    验证 Token 有效性，返回 customer_id 或 None
    """
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        customer_id, expire_ts, signature = int(parts[0]), int(parts[1]), parts[2]

        # 检查过期
        if time.time() > expire_ts:
            return None

        # 验证签名
        payload = f"{customer_id}.{expire_ts}"
        expected = hmac.new(
            settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()[:32]
        if not hmac.compare_digest(signature, expected):
            return None

        return customer_id
    except Exception:
        return None


def _get_config(env_key, settings_key):
    """优先从环境变量获取，降级到 SystemSettings"""
    import os
    val = os.environ.get(env_key)
    if val:
        return val
    from apps.core.utils import get_system_settings
    return get_system_settings().get(settings_key, '')
```

### 3.3 认证中间件 / 装饰器

新建 `apps/api/mp_auth.py`：

```python
"""小程序 API 认证"""
from functools import wraps
from django.http import JsonResponse
from apps.core.services.wechat_auth_service import verify_token
from apps.core.models import WechatCustomer


def mp_login_required(view_func):
    """要求小程序登录的装饰器"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return JsonResponse({'error': '未登录'}, status=401)

        token = auth_header[7:]
        customer_id = verify_token(token)
        if not customer_id:
            return JsonResponse({'error': '登录已过期'}, status=401)

        try:
            request.mp_customer = WechatCustomer.objects.get(
                id=customer_id, is_active=True
            )
        except WechatCustomer.DoesNotExist:
            return JsonResponse({'error': '用户不存在'}, status=401)

        return view_func(request, *args, **kwargs)
    return wrapper


def mp_login_optional(view_func):
    """可选登录的装饰器（未登录也能访问，但尝试解析用户）"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        request.mp_customer = None
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
            customer_id = verify_token(token)
            if customer_id:
                try:
                    request.mp_customer = WechatCustomer.objects.get(
                        id=customer_id, is_active=True
                    )
                except WechatCustomer.DoesNotExist:
                    pass
        return view_func(request, *args, **kwargs)
    return wrapper
```

---

## 四、P3 — 小程序 API 接口开发

### 4.1 路由配置

**`apps/api/mp_urls.py`**（新建）：

```python
from django.urls import path
from . import mp_views

urlpatterns = [
    path('login/', mp_views.mp_login, name='mp_login'),
    path('skus/', mp_views.mp_sku_list, name='mp_sku_list'),
    path('skus/<int:pk>/', mp_views.mp_sku_detail, name='mp_sku_detail'),
    path('reservations/', mp_views.mp_create_reservation, name='mp_create_reservation'),
    path('my-reservations/', mp_views.mp_my_reservations, name='mp_my_reservations'),
    path('my-reservations/<int:pk>/', mp_views.mp_reservation_detail, name='mp_reservation_detail'),
]
```

**`config/urls.py` 新增**：

```python
path('api/mp/', include('apps.api.mp_urls')),
```

### 4.2 视图实现

**`apps/api/mp_views.py`**（新建）：

```python
"""微信小程序 API 视图"""
import json
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from apps.core.models import SKU, SKUImage, SKUComponent, Reservation, WechatCustomer
from apps.core.services.wechat_auth_service import code_to_session, generate_token
from .mp_auth import mp_login_required, mp_login_optional


@csrf_exempt
@require_http_methods(["POST"])
def mp_login(request):
    """微信登录"""
    try:
        body = json.loads(request.body)
        code = body.get('code', '')
        if not code:
            return JsonResponse({'error': 'code 不能为空'}, status=400)

        # 调微信接口换 openid
        wx_data = code_to_session(code)
        openid = wx_data['openid']

        # 查找或创建客户
        customer, created = WechatCustomer.objects.get_or_create(
            openid=openid,
            defaults={
                'unionid': wx_data.get('unionid', ''),
            }
        )
        if not created:
            customer.save(update_fields=['updated_at'])  # 更新最近访问时间

        # 生成 Token
        token = generate_token(customer.id, openid)

        return JsonResponse({
            'token': token,
            'customer': {
                'id': customer.id,
                'nickname': customer.nickname,
                'phone': customer.phone,
                'is_new': created,
            }
        })
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'error': '登录失败，请稍后重试'}, status=500)


@require_http_methods(["GET"])
@mp_login_optional
def mp_sku_list(request):
    """产品列表"""
    qs = SKU.objects.filter(is_active=True, mp_visible=True).order_by('mp_sort_order', 'id')

    # 分类筛选
    category = request.GET.get('category')
    if category:
        qs = qs.filter(category=category)

    # 关键词搜索
    keyword = request.GET.get('keyword')
    if keyword:
        qs = qs.filter(name__icontains=keyword)

    results = []
    for sku in qs:
        # 获取封面图
        cover = _get_cover_image(sku)

        # 计算库存状态
        stock_status = _calc_stock_status(sku)

        results.append({
            'id': sku.id,
            'name': sku.name,
            'category': sku.category,
            'cover_image': cover,
            'rental_price': str(sku.rental_price),
            'deposit': str(sku.deposit),
            'display_stock': sku.display_stock,
            'stock_status': stock_status,
            'description': sku.description[:100] if sku.description else '',
        })

    return JsonResponse({'results': results})


@require_http_methods(["GET"])
@mp_login_optional
def mp_sku_detail(request, pk):
    """产品详情"""
    try:
        sku = SKU.objects.get(pk=pk, is_active=True, mp_visible=True)
    except SKU.DoesNotExist:
        return JsonResponse({'error': '产品不存在'}, status=404)

    # 多图
    images = list(sku.images.all().values('image', 'is_cover', 'sort_order'))
    image_list = [{'url': request.build_absolute_uri(f'/media/{img["image"]}'),
                   'is_cover': img['is_cover']} for img in images]
    # 如果没有多图，降级使用 SKU.image
    if not image_list and sku.image:
        image_list = [{'url': request.build_absolute_uri(sku.image.url), 'is_cover': True}]

    # 部件列表（仅展示名称、规格、数量）
    components = list(
        SKUComponent.objects.filter(sku=sku)
        .select_related('part')
        .values('part__name', 'part__spec', 'quantity_per_set')
    )
    component_list = [{
        'name': c['part__name'],
        'spec': c['part__spec'],
        'quantity': c['quantity_per_set'],
    } for c in components]

    stock_status = _calc_stock_status(sku)

    return JsonResponse({
        'id': sku.id,
        'name': sku.name,
        'category': sku.category,
        'images': image_list,
        'rental_price': str(sku.rental_price),
        'deposit': str(sku.deposit),
        'display_stock': sku.display_stock,
        'stock_status': stock_status,
        'description': sku.description,
        'components': component_list,
    })


@csrf_exempt
@require_http_methods(["POST"])
@mp_login_required
def mp_create_reservation(request):
    """提交意向订单"""
    try:
        body = json.loads(request.body)

        # 参数校验
        sku_id = body.get('sku_id')
        event_date = body.get('event_date')
        customer_wechat = body.get('customer_wechat', '')

        if not sku_id:
            return JsonResponse({'error': '请选择产品'}, status=400)
        if not event_date:
            return JsonResponse({'error': '请选择日期'}, status=400)
        if not customer_wechat:
            return JsonResponse({'error': '请填写微信号'}, status=400)

        # 验证 SKU
        try:
            sku = SKU.objects.get(pk=sku_id, is_active=True, mp_visible=True)
        except SKU.DoesNotExist:
            return JsonResponse({'error': '产品不存在或已下架'}, status=400)

        # 防刷：单客户每日最多 10 个意向订单
        from django.utils import timezone
        today = timezone.localdate()
        today_count = Reservation.objects.filter(
            wechat_customer=request.mp_customer,
            source='miniprogram',
            created_at__date=today,
        ).count()
        if today_count >= 10:
            return JsonResponse({'error': '今日提交次数已达上限，请明日再试'}, status=429)

        # 创建预定单
        reservation = Reservation(
            customer_wechat=customer_wechat,
            customer_name=body.get('customer_name', ''),
            customer_phone=body.get('customer_phone', ''),
            city=body.get('city', ''),
            sku=sku,
            quantity=body.get('quantity', 1),
            event_date=event_date,
            notes=body.get('notes', ''),
            source='miniprogram',
            wechat_customer=request.mp_customer,
            # created_by 留空（非内部用户）
            # owner 留空（由后台客服手动分配或走系统默认）
        )
        reservation.save()

        return JsonResponse({
            'reservation_no': reservation.reservation_no,
            'message': '意向订单提交成功，客服将尽快通过微信与您联系确认',
        }, status=201)

    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)
    except Exception as e:
        return JsonResponse({'error': '提交失败，请稍后重试'}, status=500)


@require_http_methods(["GET"])
@mp_login_required
def mp_my_reservations(request):
    """我的意向订单列表"""
    qs = Reservation.objects.filter(
        wechat_customer=request.mp_customer,
        source='miniprogram',
    ).select_related('sku').order_by('-created_at')

    # 状态映射（面向客户的友好文案）
    STATUS_MAP = {
        'pending_info': '待客服确认',
        'ready_to_convert': '确认中',
        'converted': '已下单',
        'cancelled': '已取消',
        'refunded': '已退款',
    }

    results = []
    for r in qs:
        cover = _get_cover_image(r.sku) if r.sku else ''
        results.append({
            'id': r.id,
            'reservation_no': r.reservation_no,
            'sku_name': r.sku.name if r.sku else '',
            'sku_cover_image': cover,
            'event_date': str(r.event_date),
            'quantity': r.quantity,
            'deposit_amount': str(r.deposit_amount),
            'status': r.status,
            'status_label': STATUS_MAP.get(r.status, r.get_status_display()),
            'created_at': r.created_at.isoformat(),
        })

    return JsonResponse({'results': results})


@require_http_methods(["GET"])
@mp_login_required
def mp_reservation_detail(request, pk):
    """意向订单详情"""
    try:
        reservation = Reservation.objects.select_related('sku').get(
            pk=pk,
            wechat_customer=request.mp_customer,
            source='miniprogram',
        )
    except Reservation.DoesNotExist:
        return JsonResponse({'error': '订单不存在'}, status=404)

    STATUS_MAP = {
        'pending_info': '待客服确认',
        'ready_to_convert': '确认中',
        'converted': '已下单',
        'cancelled': '已取消',
        'refunded': '已退款',
    }

    cover = _get_cover_image(reservation.sku) if reservation.sku else ''

    return JsonResponse({
        'id': reservation.id,
        'reservation_no': reservation.reservation_no,
        'sku_name': reservation.sku.name if reservation.sku else '',
        'sku_cover_image': cover,
        'event_date': str(reservation.event_date),
        'quantity': reservation.quantity,
        'city': reservation.city,
        'customer_name': reservation.customer_name,
        'customer_phone': reservation.customer_phone,
        'customer_wechat': reservation.customer_wechat,
        'deposit_amount': str(reservation.deposit_amount),
        'notes': reservation.notes,
        'status': reservation.status,
        'status_label': STATUS_MAP.get(reservation.status, reservation.get_status_display()),
        'created_at': reservation.created_at.isoformat(),
    })


# ---------- 辅助函数 ----------

def _get_cover_image(sku):
    """获取 SKU 封面图 URL"""
    cover_img = SKUImage.objects.filter(sku=sku, is_cover=True).first()
    if cover_img and cover_img.image:
        return cover_img.image.url
    if not cover_img:
        first_img = SKUImage.objects.filter(sku=sku).first()
        if first_img and first_img.image:
            return first_img.image.url
    if sku.image:
        return sku.image.url
    return ''


def _calc_stock_status(sku):
    """计算营销库存状态"""
    if sku.display_stock <= 0:
        return 'soldout'
    if sku.display_stock_warning > 0 and sku.display_stock <= sku.display_stock_warning:
        return 'warning'
    return 'normal'
```

---

## 五、P4 — 后台 SKU 管理页面扩展

### 5.1 SKU 编辑表单新增区域

在现有 `templates/skus.html` 的 SKU 编辑弹窗中，新增"小程序设置"折叠区域：

```html
<!-- 小程序设置区域 -->
<div class="form-section">
    <h4>小程序设置</h4>
    <div class="form-row">
        <label>小程序可见</label>
        <input type="checkbox" name="mp_visible" />
    </div>
    <div class="form-row">
        <label>展示库存</label>
        <input type="number" name="display_stock" min="0" value="0" />
    </div>
    <div class="form-row">
        <label>库存预警线</label>
        <input type="number" name="display_stock_warning" min="0" value="0" />
        <small>低于此值小程序显示"即将售罄"</small>
    </div>
    <div class="form-row">
        <label>排序权重</label>
        <input type="number" name="mp_sort_order" value="0" />
        <small>数字越小越靠前</small>
    </div>
</div>
```

### 5.2 SKU 图片管理

在 SKU 编辑页面新增"展示图片"管理区域，支持：
- 上传多张图片
- 拖拽排序
- 设置封面图（点击标记）
- 删除图片

> 实现方式：使用现有的内联表单提交模式（与 BOM 编辑类似），不引入新的前端框架。

### 5.3 预定单列表来源筛选

在 `templates/reservations/list.html` 的筛选区域新增：

```html
<select name="source">
    <option value="">全部来源</option>
    <option value="manual">客服录入</option>
    <option value="miniprogram">小程序</option>
</select>
```

同时在列表表格中新增"来源"列，小程序来源的行显示标签。

---

## 六、P5 — 测试计划

### 6.1 后端接口测试

在 `apps/core/tests.py` 中新增测试类 `MiniProgramAPITestCase`：

```python
class MiniProgramAPITestCase(TestCase):
    """小程序 API 接口测试"""

    def setUp(self):
        # 创建测试 SKU（mp_visible=True）
        # 创建测试 WechatCustomer
        pass

    # --- 产品列表 ---
    def test_sku_list_only_visible(self):
        """只返回 mp_visible=True 的产品"""
        pass

    def test_sku_list_category_filter(self):
        """分类筛选"""
        pass

    def test_sku_list_keyword_search(self):
        """关键词搜索"""
        pass

    def test_sku_list_stock_status(self):
        """库存状态计算（normal/warning/soldout）"""
        pass

    # --- 产品详情 ---
    def test_sku_detail_with_components(self):
        """产品详情包含部件列表"""
        pass

    def test_sku_detail_not_visible_404(self):
        """不可见的产品返回404"""
        pass

    def test_sku_detail_images_fallback(self):
        """多图降级到 SKU.image"""
        pass

    # --- 意向下单 ---
    def test_create_reservation_success(self):
        """正常提交意向订单"""
        pass

    def test_create_reservation_requires_login(self):
        """未登录不能提交"""
        pass

    def test_create_reservation_daily_limit(self):
        """每日提交上限10个"""
        pass

    def test_create_reservation_invalid_sku(self):
        """下架/不可见的 SKU 不能下单"""
        pass

    # --- 我的订单 ---
    def test_my_reservations_only_own(self):
        """只能看到自己的意向订单"""
        pass

    def test_my_reservations_status_label(self):
        """状态文案正确映射"""
        pass

    # --- 认证 ---
    def test_token_expired(self):
        """过期 token 被拒绝"""
        pass

    def test_invalid_token_format(self):
        """格式错误的 token 被拒绝"""
        pass
```

### 6.2 模型字段测试

```python
class MiniProgramModelTestCase(TestCase):
    """小程序相关模型测试"""

    def test_sku_display_stock_independent(self):
        """display_stock 不影响 effective_stock"""
        pass

    def test_reservation_source_field(self):
        """预定单来源字段正确存储"""
        pass

    def test_wechat_customer_unique_openid(self):
        """openid 唯一约束"""
        pass
```

### 6.3 运行测试

```bash
# 运行所有小程序相关测试
python manage.py test apps.core.tests.MiniProgramAPITestCase
python manage.py test apps.core.tests.MiniProgramModelTestCase

# 运行全部测试确认无回归
python manage.py test apps.core
```

---

## 七、配置与部署

### 7.1 环境变量

```env
# .env.prod 新增
MP_APPID=wx你的小程序appid
MP_SECRET=你的小程序secret
```

### 7.2 Django 设置

无需修改 `settings_common.py`，因为：
- 不引入新的第三方库（不修改 `requirements.txt`）
- 认证使用自定义方案，不修改 `AUTHENTICATION_BACKENDS`
- 小程序 API 使用 `@csrf_exempt`，不影响现有 CSRF 配置

### 7.3 CORS 配置（如需要）

如果小程序使用 uni-app 的 H5 模式调试，可能需要 CORS。生产环境小程序直接请求不需要 CORS。

```python
# 如果需要，在 config/settings_dev.py 中临时添加
# 不建议在生产环境启用
MIDDLEWARE = [
    'apps.core.middleware.SimpleCORSMiddleware',  # 仅开发用
    ...
]
```

### 7.4 Nginx 配置（如果使用 Nginx）

```nginx
# deploy/nginx.prod.conf 新增
# 小程序 API 路由
location /api/mp/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

---

## 八、文件清单

### 新增文件

| 文件 | 说明 |
|---|---|
| `apps/core/services/wechat_auth_service.py` | 微信登录 + Token 认证服务 |
| `apps/api/mp_auth.py` | 小程序 API 认证装饰器 |
| `apps/api/mp_urls.py` | 小程序 API 路由 |
| `apps/api/mp_views.py` | 小程序 API 视图 |
| `apps/core/migrations/0025_*.py` | 数据库迁移（自动生成） |

### 修改文件

| 文件 | 改动 |
|---|---|
| `apps/core/models.py` | 新增 `WechatCustomer`、`SKUImage` 模型，SKU 新增 4 字段，Reservation 新增 2 字段，Order 来源枚举 +1 |
| `config/urls.py` | 新增 `/api/mp/` 路由 include |
| `templates/skus.html` | SKU 编辑弹窗新增"小程序设置"区域 |
| `templates/reservations/list.html` | 新增来源筛选和来源列 |
| `apps/core/tests.py` | 新增小程序 API 测试类 |

### 不修改的文件

- `apps/core/services/order_service.py` — 不改动
- `apps/core/services/inventory_unit_service.py` — 不改动
- `apps/core/views.py` — 不改动
- `apps/api/views.py` — 不改动（内部管理 API 不受影响）
- `requirements.txt` — 不新增依赖

---

## 九、开发顺序建议

```
第 1 步：P1 模型层改动 + 迁移 + check
         ↓
第 2 步：P4 后台 SKU 管理页面扩展（可以先手动录入小程序展示数据）
         ↓
第 3 步：P2 微信登录认证（需要小程序 AppID/Secret）
         ↓
第 4 步：P3 小程序 API 接口开发
         ↓
第 5 步：P5 测试
         ↓
第 6 步：P6 小程序前端开发（独立工程）
```

> **说明**：第 1-2 步可以先做，不依赖微信配置。第 3-4 步需要微信小程序注册完成后才能联调。

---

## 变更记录

| 日期 | 版本 | 变更内容 |
|---|---|---|
| 2026-03-22 | v1.0 | 初始版本 |
