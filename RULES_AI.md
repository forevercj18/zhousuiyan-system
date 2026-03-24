# RULES_AI.md
## 1. 总原则
本仓库已经不是“从零开发”，而是一个持续迭代中的真实业务系统。后续 AI/开发者必须遵守：

- 先理解真实代码，再动手
- 优先补强现有链路，不随意重构
- 优先保证业务闭环，不优先追求架构美观
- 所有新功能都要考虑历史数据兼容
- 改完必须同步 `TASKS_AI.md`

## 2. 当前系统边界
当前仓库实际包含三层：

1. Django 后台
2. Django API（含 `/api/mp/`）
3. 微信小程序前端（`miniprogram/`）

开发时不要只盯后台模板，要判断需求属于哪一层：

- 后台管理逻辑：`apps/core/` + `templates/`
- 小程序接口：`apps/api/mp_views.py`
- 小程序前端：`miniprogram/`

## 3. 目录使用规则
### 3.1 小程序目录
当前主小程序工程是：

- `miniprogram/`

旧目录：

- `zhousuiyan-mp/`

处理原则：

- 默认继续开发 `miniprogram/`
- 不要把新代码误写到 `zhousuiyan-mp/`
- 如果要删除/归档 `zhousuiyan-mp/`，必须先确认没有人在用

### 3.2 业务代码优先级
涉及业务规则时，优先检查：

1. `apps/core/models.py`
2. `apps/core/services/`
3. `apps/core/views.py`
4. `apps/core/utils.py`
5. 对应模板 / API

不要先在模板里硬写规则。

## 4. 修改前必须确认的事
### 4.1 先看真实链路
涉及以下模块时，必须先看关联链路：

- 订单：`Order`、`OrderItem`、`FinanceTransaction`
- 预定：`Reservation`
- 转寄：转寄候选、转寄任务、来源挂靠
- 库存：`InventoryUnit`、SKU、部件库存
- 小程序：`WechatCustomer`、`SKUImage`、`/api/mp/`

### 4.2 先判断是不是“兼容型修改”
近期仓库已经经历多轮增量迭代，很多功能是“后补”的。  
因此新增需求前必须先判断：

- 历史数据是否可能为空
- 旧页面是否仍会访问旧字段
- 旧逻辑是否承担兼容职责

典型高风险字段：

- `Reservation.owner`
- `Reservation.source`
- `Reservation.city`
- `Order.order_source`
- `Order.return_service_*`
- 小程序新增展示字段

### 4.3 不把开发中文档当真相
以真实代码为准。  
文档如果过时，要同步更新，但不能反过来拿旧文档覆盖当前代码逻辑。

## 5. 修改中的规则
### 5.1 服务层优先
涉及订单、库存、财务、预定、微信认证时，优先复用：

- `apps/core/services/order_service.py`
- `apps/core/services/inventory_unit_service.py`
- `apps/core/services/assembly_service.py`
- `apps/core/services/wechat_auth_service.py`

### 5.2 状态机不能随意放宽
以下状态相关逻辑不能凭感觉放宽：

- 订单状态
- 转寄任务状态
- 预定单状态
- 包回邮服务状态
- 上门取件状态

原则：

- 前端显示要和后端校验一致
- 绕过前端时，后端仍要拒绝非法状态流转

### 5.3 财务口径不能混
以下钱不能混成一个字段/备注：

- 订单押金
- 订单尾款
- 预定订金
- 包回邮服务费

凡是涉及收款、退款、结转，优先看 `FinanceTransaction` 类型是否已存在。

### 5.4 小程序展示库存不等于真实库存
小程序当前设计里：

- `display_stock`
- `display_stock_warning`

是展示/营销字段，不代表真实可租库存。  
不要把这两个字段误接到正式库存扣减逻辑上。

### 5.5 后台模板必须做空值兼容
模板层禁止直接假设所有后加字段都有值。  
尤其对：

- 负责人
- 来源
- 城市
- 平台单号
- 支付参考号
- 小程序客户信息

都要有 `if` 或合理的 `default` 兜底。

## 6. 修改后必须做的事
### 6.1 最低验证要求
至少执行：

```powershell
python manage.py check
```

并补或执行与改动直接相关的测试。  
常见测试入口：

```powershell
python manage.py test apps.core.tests -v 1
python manage.py test apps.api.tests -v 1
```

### 6.2 文档同步
只要本次改动影响到：

- 项目结构
- 主模块边界
- 状态流转
- 小程序目录/接口
- 部署方式

就必须同步更新以下至少一个：

- `README_AI.md`
- `RULES_AI.md`
- `TASKS_AI.md`

### 6.3 每次任务完成后更新 TASKS_AI.md
必须同步：

- 本次已完成什么
- 当前下一步建议是什么

## 7. 当前高风险区域
后续开发需要特别谨慎的部分：

- `templates/orders/list.html`
- `templates/transfers.html`
- `templates/skus.html`
- `templates/reservations/list.html`
- `templates/users.html`
- `apps/core/views.py`
- `apps/core/utils.py`

原因：

- 页面脚本较多
- 状态判断密集
- 展示字段和真实业务字段容易混

## 8. 当前优先开发主线
后续应优先围绕这些真实主线推进：

- 订单与转寄稳定性
- 预定单到正式订单的连续跟进
- 包回邮服务的履约与财务闭环
- 工作台提醒与负责人机制
- 微信小程序与后台数据链路联调
- 生产环境稳定上线

不要偏离这些主线去做无关的大型重构。
