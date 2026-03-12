# 单套库存节点矩阵（V1）

日期：2026-03-11  
用途：作为“单套库存唯一编号（Unit）”在系统中的状态、节点、流转、异常、导出口径的统一基准。  
适用范围：`在外库存看板`、`转寄中心`、`订单中心`、`单套链路导出`。

---

## 1. 目标

这份文档解决一个核心问题：

1. 系统里每一套货到底现在在哪里
2. 它是从仓库发出的，还是在转寄途中
3. 它当前挂在哪个订单上
4. 它经历过哪些节点
5. 它是否异常、超时、待处理

文档基准对象：

1. `InventoryUnit`：单套库存实体
2. `UnitMovement`：单套节点日志

---

## 2. 单套库存对象定义

### 2.1 InventoryUnit

单套库存是“真实一套货”的系统编号实例，核心字段包括：

1. `unit_no`：单套编号
2. `sku`：所属 SKU
3. `status`：当前状态
4. `current_order`：当前归属订单
5. `current_location_type`：当前位置类型
6. `last_tracking_no`：最近物流单号

### 2.2 编码规则

当前规则：

1. `ZSY-SKU编码-0001`
2. 示例：`ZSY-ZSY0001-0001`

该编号必须：

1. 全局唯一
2. 生命周期内不变
3. 所有出库、转寄、回仓节点都绑定到该编号

---

## 3. InventoryUnit 当前状态矩阵

### 3.1 状态定义

当前系统模型中的状态：

1. `in_warehouse`：在库
2. `in_transit`：在途
3. `maintenance`：维修中
4. `scrapped`：已报废

### 3.2 状态含义

| 状态 | 业务含义 | 是否计入仓内可用 | 是否计入在途数量 | 备注 |
|---|---|---|---|---|
| `in_warehouse` | 单套已回仓，可再次分配 | 是 | 否 | 正常可用库存 |
| `in_transit` | 单套在履约/转寄链路上 | 否 | 是 | 统一口径，不再拆“客户持有/物流在途” |
| `maintenance` | 单套异常下线维修 | 否 | 否 | 不计可用，不计在途 |
| `scrapped` | 单套报废退出流转 | 否 | 否 | 不参与后续调度 |

### 3.3 当前位置类型

当前系统模型中的位置类型：

1. `warehouse`：仓库
2. `order`：订单
3. `transit`：物流在途
4. `unknown`：未知

### 3.4 推荐解释口径

| status | current_location_type | 推荐解释 |
|---|---|---|
| `in_warehouse` | `warehouse` | 在库 |
| `in_transit` | `transit` | 在途 |
| `in_transit` | `order` | 挂在某订单名下的在外库存 |
| `maintenance` | 任意 | 维修中 |
| `scrapped` | 任意 | 已报废 |

说明：

1. 当前系统统计口径已确定为“只分在库 / 在途”，不再强拆“客户持有”与“物流在途”。
2. 如果后续需要更精细节点，可以保留 `current_location_type` 继续拓展，但总账口径仍建议保持“在库 / 在途 / 非可用”三段式。

---

## 4. UnitMovement 节点类型矩阵

### 4.1 当前节点类型

系统当前已实现的事件类型：

1. `WAREHOUSE_OUT`：仓库发出
2. `TRANSFER_PENDING`：转寄待执行
3. `TRANSFER_SHIPPED`：转寄寄出
4. `TRANSFER_COMPLETED`：转寄完成
5. `RETURN_SHIPPED`：回仓在途
6. `RETURNED_WAREHOUSE`：已回仓
7. `EXCEPTION`：异常

### 4.2 当前节点状态

1. `normal`：正常
2. `warning`：预警
3. `timeout`：超时
4. `closed`：闭环完成

### 4.3 节点说明矩阵

| event_type | 节点中文名 | 触发时机 | 会不会改变 InventoryUnit | 是否闭环节点 |
|---|---|---|---|---|
| `WAREHOUSE_OUT` | 仓库发出 | 仓库直接给订单发货 | 会，置为 `in_transit` | 否 |
| `TRANSFER_PENDING` | 转寄待执行 | 已生成任务但尚未执行 | 不一定 | 否 |
| `TRANSFER_SHIPPED` | 转寄寄出 | 来源单向目标单寄出 | 会，仍保持 `in_transit`，但归属订单将切换 | 否 |
| `TRANSFER_COMPLETED` | 转寄完成 | 目标单正式接收该单套 | 会，归属订单切换到目标订单 | 否 |
| `RETURN_SHIPPED` | 回仓在途 | 订单向仓库回寄 | 当前系统预留 | 否 |
| `RETURNED_WAREHOUSE` | 已回仓 | 单套正式回仓入库 | 会，置为 `in_warehouse` | 是 |
| `EXCEPTION` | 异常 | 分配不足/链路异常/人工异常 | 不一定 | 否 |

---

## 5. 单套主链路

### 5.1 仓库直发链路

1. `在库`
2. `WAREHOUSE_OUT`
3. `in_transit + current_order=订单A`
4. `RETURNED_WAREHOUSE`
5. `in_warehouse`

### 5.2 一次转寄链路

1. 仓库发给订单A
2. `WAREHOUSE_OUT`
3. 单套归属 `订单A`
4. 订单A成为来源单，生成转寄任务
5. `TRANSFER_PENDING`
6. `TRANSFER_SHIPPED`
7. 单套从 `订单A` 切到 `订单B`
8. `TRANSFER_COMPLETED`
9. 后续订单B再回仓
10. `RETURNED_WAREHOUSE`

### 5.3 多次转寄链路

1. 仓库 -> A
2. A -> B
3. B -> C
4. C -> 回仓

单套编号全程不变，只改变：

1. 当前归属订单
2. 最近物流单号
3. 节点时间线

---

## 6. 系统已实现的状态变化

### 6.1 仓库发货

触发服务：

1. `InventoryUnitService.assign_units_for_order`

系统行为：

1. 从 `in_warehouse` 中选取可用单套
2. 更新单套：
   - `status = in_transit`
   - `current_order = 目标订单`
   - `current_location_type = transit`
3. 记录节点：
   - `WAREHOUSE_OUT`

### 6.2 转寄完成

触发服务：

1. `InventoryUnitService.transfer_to_target`

系统行为：

1. 找到当前归属来源订单的单套
2. 写入节点：
   - `TRANSFER_SHIPPED`
3. 更新单套：
   - `current_order = 目标订单`
   - `status = in_transit`
   - `current_location_type = transit`
4. 写入节点：
   - `TRANSFER_COMPLETED`

### 6.3 回仓归还

触发服务：

1. `InventoryUnitService.return_to_warehouse`

系统行为：

1. 找到当前归属该订单的所有单套
2. 写入节点：
   - `RETURNED_WAREHOUSE`
3. 更新单套：
   - `status = in_warehouse`
   - `current_order = null`
   - `current_location_type = warehouse`

---

## 7. 推荐的节点解释口径

为了让运营人员看得懂，建议页面展示不要直接暴露数据库字段，而是统一成下列中文节点：

| 系统字段组合 | 页面展示建议 |
|---|---|
| `status=in_warehouse` | 在库 |
| `latest event = WAREHOUSE_OUT` | 仓库发出 |
| `latest event = TRANSFER_PENDING` | 待转寄执行 |
| `latest event = TRANSFER_SHIPPED` | 转寄在途 |
| `latest event = TRANSFER_COMPLETED` | 已挂靠到新订单 |
| `latest event = RETURNED_WAREHOUSE` | 已回仓 |
| `latest event = EXCEPTION` | 异常待处理 |
| `status=maintenance` | 维修中 |
| `status=scrapped` | 已报废 |

---

## 8. 统计口径

### 8.1 数量守恒

对每个 SKU：

1. `总库存 = 仓内可用 + 在途数量 + 维修中 + 已报废`

若当前看板只统计业务可见口径，则建议展示：

1. `总库存`
2. `仓内可用`
3. `在途数量`
4. `不可用数量（维修/报废）`

### 8.2 当前口径说明

当前业务已确认：

1. “客户持有”与“物流在途”不单独拆分
2. 统一计入“在途数量”
3. 数据必须精准，不能粗略估算

---

## 9. 异常节点规则

### 9.1 当前已实现异常来源

1. 仓库分配不足：期望分配数量 > 实际可分配单套
2. 转寄单套不足：转寄任务所需数量 > 来源订单实际可迁移单套

这两类情况会写入：

1. `UnitMovement.event_type = EXCEPTION`
2. `UnitMovement.status = warning`

### 9.2 后续建议补齐

建议后续增加：

1. 长时间 `TRANSFER_PENDING`
2. 长时间 `TRANSFER_SHIPPED`
3. 同一单套出现并发归属冲突
4. 单套当前归属订单与转寄任务不一致
5. 单套状态为 `in_transit` 但无最近有效节点

---

## 10. 页面落地建议

### 10.1 在外库存看板

建议以 3 层展示：

1. 总览卡片
2. 单套明细表
3. 单套拓扑图 / 时间线

### 10.2 单套明细表建议字段

1. 单套编号
2. SKU
3. 当前状态
4. 当前归属订单
5. 最近物流单号
6. 最近节点
7. 最近时间
8. 风险状态
9. 操作（看链路）

### 10.3 拓扑图展示建议

每个单套应能看出：

1. 仓库发出节点
2. 每一次转寄来源与目标
3. 最终是否回仓
4. 当前卡在哪个节点

---

## 11. 验收清单

### 11.1 正向验收

1. 新增库存后可生成单套编号
2. 仓库发货后，单套从 `in_warehouse` 变为 `in_transit`
3. 转寄完成后，单套归属订单正确切换
4. 回仓后，单套回到 `in_warehouse`
5. 单套链路导出能看到完整节点时间线

### 11.2 反向验收

1. 单套编号不能重复
2. 单套已报废后不能再参与转寄/发货
3. 单套在异常状态下应被明确标识
4. 同一时刻单套不能同时归属多个订单
5. 数量守恒不成立时必须进异常告警

---

## 12. 与其它矩阵的关系

这份文档与以下文档共同构成系统核心规则：

1. [ORDER_STATUS_MATRIX_20260311.md](e:\项目\zhousuiyan-system\docs\ORDER_STATUS_MATRIX_20260311.md)
2. [TRANSFER_STATUS_MATRIX_20260311.md](e:\项目\zhousuiyan-system\docs\TRANSFER_STATUS_MATRIX_20260311.md)

关系如下：

1. 订单矩阵：定义订单能不能流转
2. 转寄矩阵：定义转寄任务能不能执行
3. 单套节点矩阵：定义“具体这一套货”如何移动

三者必须保持一致，不能各自为政。
