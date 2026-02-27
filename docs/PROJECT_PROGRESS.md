# 项目进度总览（快速接手）

最后更新：2026-02-27
分支：`main`

## 1. 项目目标
宝宝周岁宴道具租赁系统（Django）已实现从订单创建、发货、归还、完成，到库存/转寄/采购/部件管理的一体化后台。

## 2. 仓库结构与职责（Repo Map）
- `apps/core/`：核心业务（订单、SKU、库存、转寄、设置、工作台、模板页面控制）
- `apps/api/`：API 聚合与对外 JSON 接口
- `templates/`：前端页面模板（订单、工作台、工作台首页、设置、排期等）
- `static/`：前端静态资源（CSS/JS）
- `scripts/`：辅助脚本（如验收报告自动生成）
- `docs/`：交付与验收文档（报告模板、历史报告）
- `start.bat` / `start.ps1`：一键启动脚本（含环境检查、可选验收流程）

## 3. 当前实现进度

### 3.1 订单主流程
- [x] 创建订单（租金与押金拆分）
- [x] 状态流转：待处理 -> 待发货 -> 已发货 -> 已归还 -> 已完成
- [x] 取消订单（释放转寄锁）
- [x] 订单详情美化与状态操作统一

### 3.2 金额口径
- [x] 订单金额仅统计租金（不含押金）
- [x] 押金单独字段显示和管理

### 3.3 库存机制（已改为仓库实时可用）
- [x] 可用库存按“当前未回仓占用”计算（不按时间复用）
- [x] 支持超占显示（`overbooked_count`）
- [x] 下单前端可见实时可用数，库存不足阻止提交

### 3.4 转寄机制（本阶段重点）
- [x] 新建订单阶段自动匹配转寄候选
- [x] 规则：同 SKU、来源单已发货、来源预定日期 <= 目标预定日期-6 天
- [x] 候选排序：来源预定日期优先，其次地址距离
- [x] 锁单窗口：目标预定日期 ±5 天，防重复挂单
- [x] `TransferAllocation` 落库（locked/released/consumed）
- [x] 前端提示“转寄+仓库”组合建议
- [x] 每条明细支持手选来源单（可选）并优先挂靠
- [x] 订单详情展示实际挂靠来源单与数量

### 3.5 列表/看板展示
- [x] 订单列表、工作台、工作台最近订单增加“转寄挂靠”展示
- [x] 工作台指标已接入仓库可用库存与转寄可用数量

### 3.6 测试与验收支持
- [x] `apps.core.tests` 已覆盖关键流程（含转寄锁与优先来源）
- [x] 验收报告模板与自动记录脚本已提供

## 4. 关键入口与数据流

### 4.1 下单请求到库存/转寄决策
1. 页面：`templates/orders/form.html`
2. 前端校验接口：
   - `/api/check-availability/`
   - `/api/transfer-match/`
3. 视图：`apps/core/views.py::order_create`
4. 服务层：`apps/core/services/order_service.py::create_order`
5. 工具层：`apps/core/utils.py`
   - `check_sku_availability`
   - `build_transfer_allocation_plan`
6. 落库：
   - `Order` / `OrderItem`
   - `TransferAllocation`（创建即锁）

### 4.2 状态流转
- 工作台按钮 -> `apps/core/views.py` 各 `order_mark_*` -> `OrderService` -> 更新订单状态/日志

### 4.3 转寄可视化
- 列表页通过 `order.transfer_allocations_target.all` 渲染挂靠来源单
- 详情页显示按 SKU 聚合后的挂靠结果

## 5. 已知风险与技术债（优先级从高到低）
1. 模板改动较多，仍需一次全页人工回归（尤其工作台多 Tab 表格对齐）。
2. `TransferAllocation.status='consumed'` 的业务时机尚未完全闭环（当前主要用 locked/released）。
3. 地址距离采用字符串相似度，非地理距离，精度有限。
4. 订单编辑流程目前未完整支持“修改后重算/重锁转寄”。
5. 多人并发下单需进一步强化数据库级并发保护（当前有事务但可继续收紧）。
6. 日历排期与库存锁单联动仍可深化（当前展示为主）。
7. 部分历史页面样式仍偏旧，响应式尚需统一体验验收。
8. API 错误信息对前端提示可再细化（便于运营定位问题）。
9. 测试以核心服务为主，前端交互自动化测试不足。
10. 文档与实际界面文案需持续同步，避免培训成本上升。

## 6. 本地运行与验证

### 6.1 一键启动
- `./start.bat -Port 9000`
- 验收模式：`./start.bat -Acceptance -Port 9000`

### 6.2 核心检查
- `python manage.py check`
- `python manage.py test apps.core.tests -v 1`
- `python manage.py test apps.api.tests -v 1`

## 7. 下一步开发建议（建议顺序）
1. 转寄闭环：定义并落地 `consumed` 状态切换时机（发货/完成节点）。
2. 编辑订单重算：编辑 SKU/数量/日期/地址后，自动重算挂靠并处理旧锁释放。
3. 并发加固：对同 SKU+时间窗口加数据库层约束或更严格锁策略。
4. UI 统一：完成全站响应式与表格列宽策略统一，减少运营端视觉抖动。
5. 自动化验收：补充关键页面 E2E 冒烟脚本。

## 8. 新会话快速上手清单（给未来自己）
1. 先读本文件 + `README.md` + `README_PHASE2.md` + `DELIVERY_REPORT.md`
2. 跑 `python manage.py check` 与 `apps.core.tests`
3. 进页面优先验：新建订单 -> 转寄选择/自动匹配 -> 列表挂靠显示 -> 详情挂靠
4. 若有问题，先查：`apps/core/utils.py`、`apps/core/services/order_service.py`、`apps/core/views.py`

---
如需继续开发，请从“第 7 节第 1 条（转寄 consumed 闭环）”开始。
