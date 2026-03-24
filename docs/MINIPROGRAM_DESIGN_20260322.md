# 微信小程序——场景套装租赁展示与意向下单 设计文档

**项目名称**：宝宝周岁宴道具租赁系统 - 微信小程序端
**版本**：v1.0
**日期**：2026-03-22

---

## 一、需求概述

### 1.1 业务背景

当前后端 ERP 系统已完成订单、库存、转寄、采购、预定单等核心闭环。现需新增一个**微信小程序**作为面向 C 端客户的展示与意向下单入口，主要作用：

1. **产品展示**：展示编辑好的场景套装产品列表，客户可查看套餐包含的部件内容
2. **意向下单**：客户选购喜欢的产品套餐后提交意向订单
3. **客服对接**：客服在后台接收意向订单后，通过微信与客户沟通确认
4. **支付引导**：确认无误后，客服推送闲鱼链接给客户下单；部分客户先交定金（如50元），直接微信转账，不走闲鱼

### 1.2 核心定位

> **小程序 = 展示橱窗 + 意向收集器**

- 小程序**不做**库存判断、不做在线支付、不做订单状态流转
- 库存充足与否由客服人工判断（因为部件库存充足时可临时组装新套）
- 支付走微信转账（定金）或闲鱼链接（正式订单），不集成微信支付

### 1.3 营销展示库存

小程序可展示"剩余库存"，但此库存为**营销展示用途**，由运营人员手动设置，不等于后台真实库存。同时支持设置预警线，用于前端展示"即将售罄"等营销提示。

---

## 二、系统架构

### 2.1 整体架构

```
┌─────────────────┐     HTTPS/JSON     ┌─────────────────────────────┐
│   微信小程序      │ ◄──────────────► │   Django 后端（现有系统扩展）    │
│  (wx/uniapp)    │                    │                             │
│                 │                    │  ┌─────────────────────┐    │
│  - 产品列表      │   /api/mp/*       │  │ 小程序专用 API 层     │    │
│  - 产品详情      │ ◄──────────────► │  │ (apps/api/mp/)       │    │
│  - 意向下单      │                    │  └────────┬────────────┘    │
│  - 我的订单      │                    │           │                 │
└─────────────────┘                    │  ┌────────▼────────────┐    │
                                       │  │ 现有服务层            │    │
                                       │  │ (apps/core/services/) │    │
                                       │  └────────┬────────────┘    │
                                       │           │                 │
                                       │  ┌────────▼────────────┐    │
                                       │  │ 现有模型层            │    │
                                       │  │ (apps/core/models.py) │    │
                                       │  └─────────────────────┘    │
                                       └─────────────────────────────┘
```

### 2.2 关键设计原则

1. **最小改动原则**：不改动现有模型核心逻辑，仅新增字段和接口
2. **API 层隔离**：小程序 API 独立为 `/api/mp/` 路径，与现有内部管理 API `/api/` 互不影响
3. **认证隔离**：小程序使用微信 openid + 自定义 Token，不复用后台 Django Session
4. **业务逻辑复用**：小程序下单最终通过现有 Reservation 模型和服务层流转

---

## 三、模型设计（后端改动）

### 3.1 新增模型

#### 3.1.1 `WechatCustomer` — 微信客户

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
```

> **说明**：`openid` 在小程序登录时自动获取，`phone` 通过微信手机号授权获取（可选），`wechat_id` 由客户手动填写（提交意向订单时）。

#### 3.1.2 `SKUImage` — SKU 多图

```python
class SKUImage(models.Model):
    """SKU展示图片（多图支持）"""
    sku = models.ForeignKey(SKU, on_delete=models.CASCADE, related_name='images')
    image = models.FileField('图片', upload_to='sku_images/')
    sort_order = models.IntegerField('排序', default=0)
    is_cover = models.BooleanField('是否封面', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'sku_images'
        verbose_name = 'SKU展示图片'
        ordering = ['sort_order', 'id']
```

> **说明**：与现有 `SKU.image` 兼容——如果 `SKUImage` 表无数据，前端降级使用 `SKU.image` 字段。

### 3.2 现有模型新增字段

#### 3.2.1 `SKU` 新增营销展示字段

```python
# SKU 模型新增字段
display_stock = models.IntegerField('展示库存', default=0,
    help_text='小程序展示用，手动设置，不影响真实库存')
display_stock_warning = models.IntegerField('展示库存预警线', default=0,
    help_text='低于此值时小程序显示"即将售罄"')
mp_visible = models.BooleanField('小程序可见', default=False,
    help_text='控制是否在小程序中展示')
mp_sort_order = models.IntegerField('小程序排序', default=0,
    help_text='小程序产品列表排序，数字越小越靠前')
```

> **说明**：
> - `display_stock` / `display_stock_warning` 与 `stock` / `effective_stock` 完全独立，纯营销用途
> - `mp_visible` 独立于 `is_active`——产品可以在后台启用但不在小程序展示
> - `mp_sort_order` 控制小程序端的展示顺序

#### 3.2.2 `Order` 来源枚举扩展

```python
# Order.ORDER_SOURCE_CHOICES 新增
('miniprogram', '小程序'),
```

#### 3.2.3 `Reservation` 新增客户关联

```python
# Reservation 模型新增字段
wechat_customer = models.ForeignKey(
    'WechatCustomer', on_delete=models.SET_NULL,
    null=True, blank=True,
    related_name='reservations',
    verbose_name='小程序客户'
)
source = models.CharField('来源渠道', max_length=20, default='manual',
    choices=[('manual', '客服录入'), ('miniprogram', '小程序')])
```

> **说明**：`source` 区分客服手动录入的预定单和小程序自动提交的预定单，不影响现有预定单流程。

---

## 四、API 设计

### 4.1 接口总览

| 接口 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/api/mp/login/` | POST | 无 | 微信登录，code 换 token |
| `/api/mp/skus/` | GET | 可选 | 产品列表（仅 `mp_visible=True`） |
| `/api/mp/skus/<id>/` | GET | 可选 | 产品详情 + 部件列表 + 多图 |
| `/api/mp/reservations/` | POST | 必须 | 提交意向订单 |
| `/api/mp/my-reservations/` | GET | 必须 | 我的意向订单列表 |
| `/api/mp/my-reservations/<id>/` | GET | 必须 | 意向订单详情 |

### 4.2 接口详细设计

#### 4.2.1 微信登录

```
POST /api/mp/login/
```

**请求参数**：
```json
{
    "code": "微信wx.login()返回的code"
}
```

**处理逻辑**：
1. 调用微信 `code2session` 接口获取 `openid` 和 `session_key`
2. 查找或创建 `WechatCustomer` 记录
3. 生成自定义 Token 返回给小程序

**返回**：
```json
{
    "token": "自定义token",
    "customer": {
        "id": 1,
        "nickname": "...",
        "phone": "...",
        "is_new": true
    }
}
```

#### 4.2.2 产品列表

```
GET /api/mp/skus/
```

**查询参数**：
- `category`：分类筛选（可选）
- `keyword`：名称搜索（可选）

**返回字段**：
```json
{
    "results": [
        {
            "id": 1,
            "name": "粉色城堡主题套装",
            "category": "主题套餐",
            "cover_image": "https://.../xxx.jpg",
            "rental_price": "168.00",
            "deposit": "200.00",
            "display_stock": 5,
            "stock_status": "normal",  // normal / warning / soldout
            "description": "..."
        }
    ]
}
```

> **`stock_status` 计算逻辑**：
> - `display_stock <= 0` → `soldout`
> - `display_stock <= display_stock_warning` → `warning`（即将售罄）
> - 其他 → `normal`

#### 4.2.3 产品详情

```
GET /api/mp/skus/<id>/
```

**返回字段**：
```json
{
    "id": 1,
    "name": "粉色城堡主题套装",
    "category": "主题套餐",
    "images": [
        {"url": "https://.../1.jpg", "is_cover": true},
        {"url": "https://.../2.jpg", "is_cover": false}
    ],
    "rental_price": "168.00",
    "deposit": "200.00",
    "display_stock": 5,
    "stock_status": "normal",
    "description": "包含...",
    "components": [
        {"name": "城堡背景板", "spec": "120x180cm", "quantity": 1},
        {"name": "气球拱门", "spec": "粉色系", "quantity": 1},
        {"name": "桌布套装", "spec": "8人位", "quantity": 1}
    ]
}
```

> **部件展示规则**：仅展示 `name`、`spec`、`quantity_per_set`，不暴露内部库存、存放位置等供应链信息。

#### 4.2.4 提交意向订单

```
POST /api/mp/reservations/
```

**请求参数**：
```json
{
    "sku_id": 1,
    "quantity": 1,
    "event_date": "2026-04-15",
    "customer_name": "张三",
    "customer_phone": "13800138000",
    "customer_wechat": "zhangsan_wx",
    "city": "广州",
    "notes": "希望多加一些气球"
}
```

**处理逻辑**：
1. 验证 SKU 存在且 `mp_visible=True`
2. 创建 `Reservation` 记录：
   - `source = 'miniprogram'`
   - `status = 'pending_info'`
   - `wechat_customer` = 当前登录客户
   - `created_by = None`（无内部操作人）
   - `owner` = 系统默认分配客服（或为空，由后台手动分配）
3. 返回预定单号

**返回**：
```json
{
    "reservation_no": "RSV20260415...",
    "message": "意向订单提交成功，客服将在24小时内通过微信与您联系确认"
}
```

#### 4.2.5 我的意向订单

```
GET /api/mp/my-reservations/
```

**返回字段**：
```json
{
    "results": [
        {
            "reservation_no": "RSV20260415...",
            "sku_name": "粉色城堡主题套装",
            "sku_cover_image": "https://...",
            "event_date": "2026-04-15",
            "quantity": 1,
            "deposit_amount": "50.00",
            "status": "pending_info",
            "status_label": "待客服确认",
            "created_at": "2026-03-22T10:00:00"
        }
    ]
}
```

> **状态映射（面向客户）**：
> - `pending_info` → "待客服确认"
> - `ready_to_convert` → "确认中"
> - `converted` → "已下单"
> - `cancelled` → "已取消"
> - `refunded` → "已退款"

---

## 五、业务流程详细设计

### 5.1 完整流程图

```
客户操作（小程序）                    客服操作（后台ERP）
──────────────                    ──────────────────

1. 微信授权登录
2. 浏览产品列表
3. 查看产品详情 + 部件列表
4. 提交意向订单 ──────────────→ 5. 收到新的 Reservation
   （填写姓名/电话/微信/              source = 'miniprogram'
    日期/城市/备注）                   status = 'pending_info'
                                  │
                                  6. 客服判断库存是否满足
                                  │  （成品可用？零件够装新的？）
                                  │
                                  7. 微信联系客户沟通确认
                                  │
                    ┌─────────────┼─────────────┐
                    │             │             │
              客户取消        需要定金        直接下单
                    │             │             │
              8a. 取消预定    8b. 客户微信     8c. 客服推
                  单（后台）      转定金           闲鱼链接
                    │             │             │
                    │        9b. 后台记录      9c. 客户闲
                    │            deposit_        鱼下单
                    │            amount          │
                    │             │         10c. 后台录入
                    │             │             闲鱼单号
                    │             │             │
                    │        10b. 客服推     11c. 转为正
                    │            闲鱼链接        式Order
                    │             │             │
                    │        11b. 转为正         │
                    │            式Order         │
                    │             │             │
                    ▼             ▼             ▼
               [已取消]     [进入现有订单流程]  [进入现有订单流程]
                           pending → confirmed → delivered → ...
```

### 5.2 客服工作台集成

现有工作台无需大改，仅需：
- 预定单列表增加 `来源` 筛选（客服录入 / 小程序）
- 预定单列表可按来源排序，方便客服优先处理小程序来的意向单
- 小程序来源的预定单在列表中加标识（如"小程序"标签）

---

## 六、后台管理扩展

### 6.1 SKU 管理页面新增

在现有 SKU 管理（`skus.html`）中新增"小程序设置"区域：

| 字段 | 控件 | 说明 |
|---|---|---|
| 小程序可见 | 开关 | 控制是否在小程序展示 |
| 展示库存 | 数字输入框 | 营销展示用的库存数量 |
| 库存预警线 | 数字输入框 | 低于此值显示"即将售罄" |
| 小程序排序 | 数字输入框 | 排序权重 |
| 展示图片 | 多图上传 | 支持拖拽排序、设置封面 |

### 6.2 微信客户管理（新页面，可选，后续扩展）

后续可新增"微信客户"管理页面，用于查看小程序注册客户列表、关联预定单等。第一版不做。

---

## 七、安全设计

### 7.1 认证机制

```
小程序端                      后端
────────                    ────
wx.login() → code ──────→ POST /api/mp/login/
                            │
                            ├─ 调微信 code2Session 换 openid
                            ├─ 查找/创建 WechatCustomer
                            ├─ 生成 Token（自定义签名，含过期时间）
                            │
Token ◄──────────────────── 返回 token
│
后续请求 Header:
Authorization: Bearer <token>
```

### 7.2 数据隔离

- 小程序 API 仅能查看 `mp_visible=True` 的 SKU
- 小程序 API 仅能查看/创建自己的 Reservation
- 小程序 API 不暴露任何内部管理数据（部件库存量、存放位置、内部用户信息等）
- 小程序 API 与内部管理 API 使用完全不同的认证体系

### 7.3 防刷策略

- 单客户每日最多提交 10 个意向订单
- 同一 openid 的登录接口限频

---

## 八、与现有系统的兼容性分析

### 8.1 不需要改动的部分

| 模块 | 说明 |
|---|---|
| Order 状态机 | 完全复用，不改动 |
| 转寄中心 | 不涉及 |
| 单套库存体系 | 不涉及 |
| 装配/维修/处置 | 不涉及 |
| 采购与部件库存 | 不涉及 |
| 审批/风控/审计 | 不涉及 |
| 财务流水 | 复用现有预定单订金流水类型 |
| 权限系统 | 不涉及（小程序用独立认证） |

### 8.2 需要改动的部分

| 改动项 | 影响范围 | 风险 |
|---|---|---|
| SKU 新增 4 个展示字段 | 仅 SKU 模型，不影响库存计算 | 极低 |
| SKUImage 新增模型 | 新增表，不影响现有 | 极低 |
| WechatCustomer 新增模型 | 新增表，不影响现有 | 极低 |
| Reservation 新增 2 个字段 | `wechat_customer`、`source`，均为可选字段 | 低 |
| Order SOURCE_CHOICES 新增 `miniprogram` | 仅新增枚举值 | 极低 |
| 新增 `/api/mp/` 路由 | 独立路径，不影响现有 API | 极低 |

---

## 九、小程序端页面规划（前端参考）

### 9.1 页面列表

| 页面 | 路径 | 功能 |
|---|---|---|
| 首页/产品列表 | `/pages/index/index` | 产品列表、分类筛选、搜索 |
| 产品详情 | `/pages/detail/detail` | 轮播图、价格、描述、部件列表、"我要租"按钮 |
| 意向下单 | `/pages/order/order` | 填写信息表单、提交 |
| 我的订单 | `/pages/my-orders/my-orders` | 意向订单列表、状态查看 |
| 订单详情 | `/pages/order-detail/order-detail` | 意向订单详细信息 |

### 9.2 UI 交互要点

- 产品列表：卡片式布局，显示封面图、名称、价格、库存状态标签
- 库存状态标签：`正常`（绿色）/ `即将售罄`（橙色）/ `已售罄`（灰色）
- 产品详情：顶部轮播图 → 价格/押金 → 描述 → 套餐包含（部件列表）→ 底部固定"我要租"按钮
- 意向下单：简洁表单，必填项最少化（微信号 + 日期 + 手机号）

---

## 变更记录

| 日期 | 版本 | 变更内容 |
|---|---|---|
| 2026-03-22 | v1.0 | 初始版本 |
