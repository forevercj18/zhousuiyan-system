# README_AI.md
## 1. 项目定位
这是一个面向“宝宝周岁宴道具租赁”场景的一体化业务系统，当前仓库已经同时包含：

- Django 后台管理系统
- Django 内部/小程序 API
- 微信小程序前端工程

核心目标不是单纯录订单，而是把以下链路放在一套系统里：

- 订单创建、发货、归还、完成、取消
- 转寄候选、转寄任务、来源挂靠
- 单套库存、部件库存、BOM、装配
- 维修、回件质检、处置
- 财务流水、审批、风险、审计、运维
- 预定单、客服跟进、负责人移交
- 包回邮服务
- 微信小程序展示与意向下单

## 2. 当前技术栈
基于真实代码，不是规划：

- 后端：Django 4.2.9
- API：Django REST framework
- 前端后台：Django Templates + Bootstrap + 原生 JS
- 数据库：
  - 开发默认 SQLite
  - 生产配置支持 PostgreSQL
- 静态资源：WhiteNoise
- 生产运行：
  - Windows：Waitress + `start_prod_windows.bat/.ps1`
  - Docker / Nginx：`docker-compose.prod.yml`、`deploy/`
- 小程序前端：微信原生小程序工程

## 3. 仓库结构总览
当前应重点关注这些目录：

```text
zhousuiyan-system/
├─ apps/
│  ├─ core/                      # 核心业务模型、视图、服务、权限、测试
│  └─ api/                       # API，含 /api/mp/ 小程序接口
├─ config/                       # Django settings、urls
├─ templates/                    # 后台模板
├─ static/                       # 后台静态资源
├─ docs/                         # 状态矩阵、部署、小程序设计文档
├─ deploy/                       # Docker/Nginx 部署样板
├─ scripts/                      # 初始化、部署、验收脚本
├─ miniprogram/                  # 当前在用的微信小程序工程
├─ zhousuiyan-mp/                # 旧的微信小程序脚手架/示例目录，非当前主工程
├─ README_AI.md
├─ RULES_AI.md
└─ TASKS_AI.md
```

## 4. 目录职责说明
### 4.1 Django 主项目
重点文件：

- `apps/core/models.py`
- `apps/core/views.py`
- `apps/core/utils.py`
- `apps/core/services/`
- `apps/core/tests.py`
- `config/urls.py`

这里承载了绝大多数业务规则。  
后续开发不要先改模板，优先确认模型、服务层、视图层是否已有对应能力。

### 4.2 后台模板
重点目录：

- `templates/orders/`
- `templates/reservations/`
- `templates/procurement/`
- `templates/dashboard.html`
- `templates/transfers.html`
- `templates/users.html`

当前后台仍是服务端模板渲染为主，前端 JS 多数为页面内局部增强，不是 SPA。

### 4.3 微信小程序后端
重点文件：

- `config/urls.py`：已注册 `path('api/mp/', include('apps.api.mp_urls'))`
- `apps/api/mp_urls.py`
- `apps/api/mp_views.py`
- `apps/api/mp_auth.py`
- `apps/core/services/wechat_auth_service.py`

当前 `/api/mp/` 已有 6 个接口：

- `POST /api/mp/login/`
- `GET /api/mp/skus/`
- `GET /api/mp/skus/<id>/`
- `POST /api/mp/reservations/`
- `GET /api/mp/my-reservations/`
- `GET /api/mp/my-reservations/<id>/`

### 4.4 微信小程序前端
当前在用目录是：

- `miniprogram/`

当前页面：

- `pages/index/`：首页 / 产品列表
- `pages/detail/`：产品详情
- `pages/order/`：提交意向单
- `pages/my-orders/`：我的意向单列表
- `pages/order-detail/`：意向单详情
- `utils/api.js`：请求与登录封装

请注意：

- `zhousuiyan-mp/` 目录是旧脚手架，不是当前主小程序工程
- 后续如继续开发小程序，默认改 `miniprogram/`

## 5. 当前已经落地的核心业务
### 5.1 订单中心
已支持：

- 新建、编辑、详情
- 发货、归还、完成、取消
- 时效状态判断与筛选
- 转寄挂靠展示
- 包回邮服务登记
- 平台来源 / 平台单号 / 回邮支付参考号搜索

### 5.2 预定单主线
已支持：

- 极简预定单录入
- 转正式订单
- 订金流水
- 负责人机制
- 联系提醒
- 负责人转交
- 跟进分布、移交建议、履约跟进看板

### 5.3 转寄中心
已支持：

- 候选池
- 推荐来源计算
- 任务生成、完成、取消
- 当前挂靠与推荐来源展示

### 5.4 仓储主线
已支持：

- SKU / BOM
- 装配单
- 单套库存
- 部件库存
- 维修
- 回件质检
- 处置

### 5.5 财务与审计
已支持：

- 财务流水
- 对账基础页面
- 包回邮服务流水
- 预定订金流水
- 审批、风险、审计、运维

### 5.6 微信小程序
后端已完成：

- 微信客户模型
- SKU 小程序展示字段
- SKU 多图模型
- 小程序登录
- 产品列表/详情
- 意向预定提交
- 我的意向单列表/详情

前端已有一版可运行的小程序工程，但是否完全联调通过，需要继续实机验证。

## 6. 当前重要模型补充认知
以下模型是近期接手时必须知道的：

- `Order`
- `Reservation`
- `FinanceTransaction`
- `InventoryUnit`
- `TransferTask`
- `SKU`
- `SKUImage`
- `WechatCustomer`

近期新增的重要字段/概念：

- `Order.order_source`
- `Order.source_order_no`
- `Order.return_service_*`
- `Reservation.owner`
- `Reservation.source`
- `Reservation.wechat_customer`
- `SKU.mp_visible`
- `SKU.display_stock`
- `SKU.display_stock_warning`
- `SKU.mp_sort_order`

## 7. 当前重要迁移
最近与接手高度相关的迁移：

- `0022_reservation_and_finance_transaction_updates`
- `0023_reservation_owner`
- `0024_order_return_service_fields`
- `0025_miniprogram_models`
- `0026_reservation_delivery_address`

接手后如果环境不一致，优先检查：

- `python manage.py showmigrations`
- `python manage.py migrate`

## 8. 运行方式
### 8.1 本地开发
优先使用：

- `start.bat`
- `start.ps1`

常用命令：

```powershell
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\python manage.py test apps.core.tests -v 1
.\.venv\Scripts\python manage.py test apps.api.tests -v 1
```

### 8.2 Windows 本机生产
优先使用：

- `start_prod_windows.bat`
- `start_prod_windows.ps1`

### 8.3 微信小程序
小程序前端目录：

- `miniprogram/`

接手时至少先看：

- `miniprogram/app.json`
- `miniprogram/utils/api.js`
- `apps/api/mp_views.py`

## 9. 推荐阅读顺序
如果是第一次接手，建议按下面顺序：

1. `README_AI.md`
2. `RULES_AI.md`
3. `TASKS_AI.md`
4. `apps/core/models.py`
5. `apps/core/services/`
6. `apps/core/views.py`
7. `apps/api/mp_views.py`
8. `templates/orders/`
9. `templates/reservations/`
10. `templates/dashboard.html`
11. `miniprogram/`
12. `docs/MINIPROGRAM_DESIGN_20260322.md`
13. `docs/MINIPROGRAM_DEV_20260322.md`

## 10. 当前接手时最容易踩坑的点
- `zhousuiyan-mp/` 不是当前主小程序工程，默认看 `miniprogram/`
- 后台很多页面仍是模板 + 页面脚本组合，不要误判成前后端分离项目
- 预定单和正式订单是两条链路，不要混
- 包回邮服务是订单附加服务，且支持发货后补录
- 旧数据可能存在后加字段为空的情况，模板层必须做空值兼容
- 有些展示字段是营销/运营字段，不等于真实库存字段，例如小程序展示库存
