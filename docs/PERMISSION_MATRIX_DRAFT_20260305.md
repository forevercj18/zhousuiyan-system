# 权限矩阵初稿（角色 × 模块 × 动作）

日期：2026-03-05  
对应任务：`Phase A / A-01`  
说明：本稿用于评审冻结，后续按“动作级 + 字段级”逐步落地。

---

## 1. 角色定义

1. `admin`：系统管理员（全量权限）
2. `manager`：业务经理（经营与流程管理）
3. `warehouse_manager`：仓库主管（仓储与调度管理）
4. `warehouse_staff`：仓库操作员（执行层）
5. `customer_service`：客服（录单与跟单）

---

## 2. 模块定义

1. `dashboard` 工作台
2. `orders` 订单中心
3. `calendar` 日历排期
4. `transfers` 转寄中心
5. `outbound_inventory` 在外库存看板
6. `skus` 产品管理
7. `procurement` 采购单
8. `parts` 部件库存/流水
9. `audit_logs` 审计日志
10. `users` 用户管理
11. `settings` 系统设置

---

## 3. 动作字典（统一动作编码）

通用动作：
1. `view` 查看
2. `create` 新建
3. `update` 编辑
4. `delete` 删除
5. `export` 导出
6. `approve` 审批

业务关键动作（建议纳入动作级权限）：
1. `order.confirm_delivery` 订单确认/发货
2. `order.mark_returned` 订单标记归还
3. `order.force_cancel` 强制取消
4. `order.change_amount` 修改金额（租金/押金）
5. `transfer.recommend` 重新推荐
6. `transfer.create_task` 生成转寄任务
7. `transfer.complete_task` 完成转寄任务
8. `transfer.cancel_task` 取消转寄任务
9. `inventory.init_units` 初始化单套编号
10. `inventory.export_topology` 导出拓扑
11. `sku.upload_image` 上传/替换产品图片
12. `parts.adjust_stock` 部件库存调整
13. `settings.update_business_rules` 修改业务规则参数

---

## 4. 模块级权限矩阵（V1）

说明：`√` 允许，`-` 禁止

| 模块 | admin | manager | warehouse_manager | warehouse_staff | customer_service |
|---|---|---|---|---|---|
| dashboard | √ | √ | √ | √ | - |
| orders | √ | √ | √ | -（仅执行动作入口） | √ |
| calendar | √ | √ | - | - | √ |
| transfers | √ | √ | √ | √ | - |
| outbound_inventory | √ | √ | √ | √ | - |
| skus | √ | - | √ | √ | - |
| procurement | √ | - | √ | - | - |
| parts | √ | - | √ | √ | - |
| audit_logs | √ | √ | √ | - | - |
| users | √ | - | - | - | - |
| settings | √ | - | - | - | - |

---

## 5. 动作级权限矩阵（V1建议）

说明：只列关键动作；未列动作按模块通用动作权限继承。

| 动作 | admin | manager | warehouse_manager | warehouse_staff | customer_service |
|---|---|---|---|---|---|
| order.confirm_delivery | √ | √ | √ | √ | - |
| order.mark_returned | √ | √ | √ | √（仅可回仓单） | - |
| order.force_cancel | √ | 需审批 | 需审批 | - | - |
| order.change_amount | √ | √ | - | - | - |
| transfer.recommend | √ | √ | √ | √ | - |
| transfer.create_task | √ | √ | √ | √（仅执行） | - |
| transfer.complete_task | √ | √ | √ | √ | - |
| transfer.cancel_task | √ | 需审批 | 需审批 | - | - |
| inventory.init_units | √ | - | √ | - | - |
| inventory.export_topology | √ | √ | √ | √ | - |
| sku.upload_image | √ | - | √ | √ | - |
| parts.adjust_stock | √ | - | √ | - | - |
| settings.update_business_rules | √ | - | - | - | - |

---

## 6. 字段级权限建议（V1）

订单字段：
1. `customer_name/customer_phone/delivery_address`：客服可编辑（仅待处理/待发货）
2. `event_date`：客服可编辑（发货后只读）
3. `total_amount/deposit_paid/balance`：仅 admin/manager 可改
4. `ship_tracking/return_tracking`：仓库角色可录入
5. `transfer allocations`：仅转寄中心可变更

系统设置字段：
1. 发货/回收/缓冲天数：仅 admin
2. 风险阈值：仅 admin
3. 仓库发货人信息：admin 可改，其他只读

---

## 7. 强约束规则（必须前后端双校验）

1. 已发货订单不可编辑基础信息与金额。
2. 转寄来源链路活跃时，禁止订单中心直接标记归还。
3. 转寄任务已完成/已取消后，不允许重复完成。
4. 审批未通过前，禁止执行高风险动作（强制取消/取消任务）。

---

## 8. 审批流建议（V1）

需要审批的动作：
1. `order.force_cancel`
2. `transfer.cancel_task`
3. `order.change_amount`（发货后）

审批角色：
1. 一级审批：`manager`
2. 二级审批（可选）：`admin`

---

## 9. 审计要求（与权限联动）

每个高风险动作至少记录：
1. 操作人、角色、时间、IP
2. 目标对象（订单号/任务ID/单套编号）
3. 操作前值与操作后值（字段diff）
4. 审批单号（若需审批）
5. 来源（页面/API/批处理）

---

## 10. 待确认项（请你拍板）

1. `warehouse_staff` 是否允许独立“生成转寄任务”，还是只能“完成已分配任务”？
2. `manager` 是否可直接取消转寄任务，还是也必须审批？
3. 客服是否允许编辑“预定日期”到发货前最后一天？
4. 导出权限是否默认开放给所有可查看角色？

---

## 11. 落地顺序建议

1. 先冻结“动作级权限表”（本文件第5节）。
2. 再实现后端动作校验（接口级）。
3. 最后做前端按钮显隐与禁用（体验层）。

