# 订单状态矩阵（V1）

日期：2026-03-11  
用途：作为订单状态流转、页面按钮展示、动作权限控制、测试验收的统一基准。  
适用范围：`订单中心`、`工作台`、`订单详情`、`转寄中心` 相关订单操作。

---

## 1. 状态机总览

系统内订单状态：

1. `pending`：待处理
2. `confirmed`：待发货
3. `delivered`：已发货
4. `in_use`：使用中
5. `returned`：已归还
6. `completed`：已完成
7. `cancelled`：已取消

当前业务主链路：

1. `待处理 -> 待发货 -> 已发货 -> 已归还 -> 已完成`
2. `待处理 -> 已取消`
3. `待发货 -> 已取消`

说明：

1. `in_use` 当前主要用于兼容历史状态与转寄链路判断，运营主界面仍以 `已发货/已归还/已完成` 为主。
2. 所有页面展示、服务层校验、审批流程都必须服从本矩阵，不允许页面行为与后端规则不一致。

---

## 2. 允许的状态跳转

| 当前状态 | 可执行动作 | 目标状态 | 说明 |
|---|---|---|---|
| `pending` | 确认订单 | `confirmed` | 录入押金后进入待发货 |
| `pending` | 确认并直接发货 | `delivered` | 录入押金与发货单号 |
| `pending` | 取消订单 | `cancelled` | 仅前置状态允许取消 |
| `confirmed` | 录入运单发货 | `delivered` | 发货完成 |
| `confirmed` | 取消订单 | `cancelled` | 仅前置状态允许取消 |
| `delivered` | 标记归还 | `returned` | 仅寄回仓库订单允许 |
| `returned` | 标记完成 | `completed` | 完成后退押金 |

---

## 3. 明确禁止的跳转

以下跳转必须被后端拒绝：

1. `delivered -> cancelled`
2. `returned -> cancelled`
3. `completed -> cancelled`
4. `cancelled -> 任意状态`
5. `delivered -> completed`
6. `pending -> returned`
7. `confirmed -> returned`
8. `pending -> completed`
9. `confirmed -> completed`

说明：

1. `已发货` 及之后状态，订单已经进入履约链路，不能再走取消。
2. `已完成`、`已取消` 都是终态，只允许查看，不允许二次流转。

---

## 4. 页面入口矩阵

### 4.1 订单中心 / 订单详情 / 工作台

| 当前状态 | 可见按钮 | 是否允许执行 | 说明 |
|---|---|---|---|
| `pending` | 编辑 | 是 | 可修改订单内容 |
| `pending` | 确认/发货 | 是 | 可仅确认，也可直接发货 |
| `pending` | 取消订单 | 是 | 仅前置状态允许 |
| `pending` | 删除 | 是 | 仅待处理状态允许删除 |
| `confirmed` | 编辑 | 是 | 可修改未发货内容 |
| `confirmed` | 录入运单发货 | 是 | 发货后进入 `delivered` |
| `confirmed` | 取消订单 | 是 | 仅前置状态允许 |
| `delivered` | 标记归还 | 条件允许 | 仅“寄回仓库”的订单允许 |
| `delivered` | 转寄中心操作 | 条件允许 | 若命中转寄链路，必须去转寄中心 |
| `returned` | 标记完成 | 是 | 进入 `completed` |
| `completed` | 查看 | 是 | 只读 |
| `cancelled` | 查看 | 是 | 只读 |

### 4.2 转寄链路特殊规则

当订单满足以下任一情况时，不能在订单中心直接“标记归还”：

1. 该订单已作为转寄来源单，被后续订单挂靠且仍处于有效链路中
2. 该订单的闭环需要通过转寄任务完成，而不是回仓归还

这类订单在订单中心应显示：

1. `转寄中心操作`

不应显示：

1. `标记归还`

---

## 5. 动作权限矩阵

### 5.1 关键动作编码

1. `order.confirm_delivery`：确认订单 / 录入运单发货
2. `order.mark_returned`：标记归还 / 标记完成
3. `order.force_cancel`：取消订单
4. `orders:update`：编辑订单
5. `orders:delete`：删除订单

### 5.2 动作与状态联合约束

| 动作 | 需要动作权限 | 允许状态 |
|---|---|---|
| 确认订单 | `order.confirm_delivery` | `pending` |
| 直接发货 | `order.confirm_delivery` | `pending` |
| 录入运单发货 | `order.confirm_delivery` | `confirmed` |
| 标记归还 | `order.mark_returned` | `delivered` / `in_use` |
| 标记完成 | `order.mark_returned` | `returned` |
| 取消订单 | `order.force_cancel` 或审批申请 | `pending` / `confirmed` |
| 编辑订单 | 模块 `orders:update` | `pending` / `confirmed` |
| 删除订单 | 模块 `orders:delete` | `pending` |

说明：

1. 页面隐藏按钮不代表安全，后端必须再次校验状态和动作权限。
2. 所有高风险动作都必须服务层拦截，防止绕过前端直接 POST。

---

## 6. 当前系统已落地规则

截至 2026-03-11，以下规则已收紧：

1. 已发货订单不能取消
2. 只有 `pending/confirmed` 才能取消
3. `order_mark_confirmed` 必须校验 `order.confirm_delivery`
4. `order_mark_completed` 只允许 `returned -> completed`
5. 订单详情页与订单列表页的“归还/转寄中心操作”规则已统一
6. 已发货且处于转寄来源链路中的订单，必须去转寄中心收尾

---

## 7. 验收清单

### 7.1 正向验收

1. `pending` 订单可以确认进入 `confirmed`
2. `pending` 订单可以直接确认并发货到 `delivered`
3. `confirmed` 订单可以录入运单发货
4. `delivered` 且非转寄链路订单可以标记归还
5. `returned` 订单可以标记完成
6. `pending/confirmed` 订单可以取消

### 7.2 反向验收

1. `delivered` 订单不能取消
2. `returned` 订单不能取消
3. `completed` 订单不能编辑
4. `delivered` 订单不能直接完成
5. 无 `order.confirm_delivery` 权限的角色不能确认订单
6. 命中转寄链路的 `delivered` 订单不能在订单中心标记归还

---

## 8. 后续建议

1. 将本矩阵进一步扩展成“状态 × 页面 × 按钮 × 权限 × 文案提示”总表
2. 为每个高风险跳转增加自动化测试
3. 在前端统一输出禁用原因提示，减少操作人员误解
4. 将该矩阵同步到培训文档与验收清单，避免口径漂移
