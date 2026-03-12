# SKU 由部件装配驱动库存方案

## 目标

- SKU 只维护套餐定义，不再直接手工录入库存。
- 套餐库存增加必须通过装配完成，并同步扣减 BOM 部件库存。
- 套餐部件折损后，通过维修工单更换部件，并同步扣减替换部件库存。

## 已落地规则

### 1. SKU 创建 / 编辑

- 新建 SKU 时，`stock` 固定为 `0`。
- 编辑 SKU 时，不允许直接修改库存数量。
- BOM 继续通过 `SKUComponent` 维护。
- 页面提示：库存请通过“新增库存（装配）”操作增加。

### 2. 装配新增库存

- 新增入口：`SKU -> 新增库存`
- 动作要求：
  - SKU 必须已配置 BOM
  - 所有部件库存必须足够
- 执行结果：
  - 生成 `AssemblyOrder`
  - 生成 `AssemblyOrderItem`
  - 扣减对应 `Part.current_stock`
  - 生成指定数量的 `InventoryUnit`
  - 同步生成 `InventoryUnitPart`
  - 同步兼容字段 `SKU.stock`

### 3. 维修换件工单

- 入口：`在外库存看板 -> 单套明细 -> 维修换件`
- 动作要求：
  - 绑定具体 `InventoryUnit`
  - 至少 1 条更换明细
  - 替换部件库存必须足够
- 执行结果：
  - 生成 `MaintenanceWorkOrder`
  - 生成 `MaintenanceWorkOrderItem`
  - 扣减替换部件库存
  - 更新对应 `InventoryUnitPart`
  - 记录审计日志

### 4. 装配取消回滚

- 入口：`装配单列表 -> 取消装配`
- 动作要求：
  - 仅已完成装配单可取消
  - 该装配单生成的单套必须全部仍在库
  - 这些单套不能已有订单占用/流转节点
- 执行结果：
  - 按装配单明细回补部件库存
  - 停用该装配单生成的单套
  - 同步兼容字段 `SKU.stock`

### 5. 维修工单取消

- 入口：`维修工单列表 / 在外库存看板`
- 动作要求：
  - 仅草稿维修工单可取消
- 执行结果：
  - 工单状态改为 `cancelled`
  - 单套恢复为 `in_warehouse`

### 5.1 维修工单反向冲销

- 入口：`维修工单列表 / 在外库存看板 -> 冲销工单`
- 动作要求：
  - 仅 `completed` 状态维修工单可冲销
  - 单套必须仍为启用状态
  - 单套当前必须在库，且未重新挂到订单
  - 不能存在后续已完成维修工单
- 执行结果：
  - 回补替换新部件库存
  - 回退单套 `InventoryUnitPart` 快照到维修前状态
  - 工单状态改为 `reversed`
  - 记录 `MAINTENANCE_REVERSED` 节点日志

### 6. 单套拆解 / 报废

- 入口：`在外库存看板 -> 单套明细`
- 支持动作：
  - `拆解回件`
  - `报废停用`
- 动作要求：
  - 单套必须未被订单占用
  - 单套必须无待执行维修工单
  - 仅 `in_warehouse / maintenance` 状态可处置
- 执行结果：
  - 拆解：按当前单套实有部件数量生成待质检回件记录，并停用单套
  - 报废：直接停用单套，不回收入库
  - 生成 `UnitDisposalOrder`
  - 同步兼容字段 `SKU.stock`

### 6.1 回件质检池

- 入口：
  - `仓储与采购 -> 回件质检池`
  - `部件库存 -> 回件质检池`
- 数据来源：
  - 单套执行 `拆解回件` 后生成的 `PartRecoveryInspection`
- 初始状态：
  - `pending`
- 可执行动作：
  - `合格回库`
  - `转待维修`
  - `报废`
- 处理结果：
  - `合格回库`：部件回补库存，记录状态改为 `returned`
  - `转待维修`：不回补库存，记录状态改为 `repair`
  - `报废`：不回补库存，记录状态改为 `scrapped`
- 二段处理：
  - 对于已转 `待维修` 的回件，可继续执行：
    - `维修完成回库`
    - `维修失败报废`
  - 二段处理完成后状态最终收敛为：
    - `returned`
    - `scrapped`
- 约束：
  - 仅 `pending` 状态可处理
  - `repair` 状态仅允许继续处理为 `returned / scrapped`
  - 已处理记录不可重复执行

### 7. 单套处置审批

- 动作编码：`unit.dispose`
- 规则：
  - 具备 `unit.dispose` 动作权限的用户可直接执行
  - 无直接权限但允许申请审批的用户，可提交审批单
  - 审批通过后才真正执行拆解/报废
- 配置项：
  - `approval_required_count_unit_dispose`
  - `approval_required_count_unit_disassemble`
  - `approval_required_count_unit_scrap`
  - 也可通过 `approval_required_count_map` 覆盖
  - JSON 支持细分键：
    - `unit.dispose.disassemble`
    - `unit.dispose.scrap`

## 新增模型

### AssemblyOrder

- `assembly_no`
- `sku`
- `quantity`
- `status`
- `notes`
- `created_by`
- `completed_at`

### AssemblyOrderItem

- `assembly_order`
- `part`
- `quantity_per_set`
- `required_quantity`
- `deducted_quantity`
- `notes`

### MaintenanceWorkOrder

- `work_order_no`
- `unit`
- `sku`
- `issue_desc`
- `status`
- `notes`
- `created_by`
- `completed_by`
- `completed_at`

### MaintenanceWorkOrderItem

- `work_order`
- `old_part`
- `new_part`
- `replace_quantity`
- `notes`

### UnitDisposalOrder

- `disposal_no`
- `action_type`
- `unit`
- `sku`
- `status`
- `issue_desc`
- `notes`
- `created_by`
- `completed_by`
- `completed_at`

### UnitDisposalOrderItem

- `disposal_order`
- `part`
- `quantity`
- `returned_quantity`
- `notes`

## 服务层

### AssemblyService

- `create_and_complete_assembly`
  - 校验 BOM
  - 校验部件库存
  - 扣减部件
  - 生成单套
  - 回写 SKU 库存

### MaintenanceService

- `create_work_order`
  - 创建维修工单
- `complete_work_order`
  - 扣减替换部件
  - 更新单套部件快照
  - 完成工单
 - `cancel_work_order`
   - 取消草稿工单
   - 单套恢复在库

### UnitDisposalService

- `create_and_complete`
  - 拆解回件
  - 报废停用
  - 回写 SKU 库存

### AssemblyService

- `cancel_assembly`
  - 装配回滚
  - 回补部件
  - 停用该装配单生成的单套

## 兼容策略

- `SKU.stock` 仍保留，但仅作为历史兼容镜像字段。
- 其来源改为：由单套数量同步回写，不再作为任何业务库存主口径。
- 页面展示、下单可用库存、SKU详情接口优先使用 `effective_stock`（激活单套聚合口径）。
- `InventoryUnitService.ensure_units_for_sku` 保留用于历史兼容与初始化。
- 新增库存主入口改为：`InventoryUnitService.create_units_for_sku`

## 当前限制

- 维修工单先在“单套维度”工作，不支持直接对抽象 SKU 批量换件。
- 单套处置已接入审批，并支持按“拆解/报废”细分审批阈值。
- `SKU.stock` 物理字段仍存在，但已降级为兼容镜像字段，不参与业务主决策。

## 当前已提供页面

- 产品管理
- 装配单
- 在外库存看板
- 部件折损池
- 回件质检池
- 维修工单
- 单套处置单
- 审批中心（可处理单套处置审批）
- 装配单 / 维修工单 / 单套处置单均支持按当前筛选条件导出 CSV
- 回件质检池支持按当前筛选条件导出 CSV
- 装配单 / 维修工单 / 单套处置单 / 回件质检池 已补齐页内统计卡

## 建议后续迭代

1. SKU 库存完全改为聚合字段，不再依赖物理列
2. 装配/维修/处置单据补充多维统计报表与趋势图
3. 回件质检池维修结果分布统计
