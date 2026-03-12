# 项目进度总览（快速接手）

最后更新：2026-03-12
分支：`main`

## 1. 项目目标
宝宝周岁宴道具租赁系统（Django）已实现从订单创建、发货、归还、完成，到库存/转寄/采购/部件管理的一体化后台。

## 1.1 生产准备状态（2026-03-12）
- [x] 已完成一轮生产前巡检（状态闭环、库存闭环、转寄闭环、入口权限一致性）
- [x] 已修复工作台/API 库存口径、订单检索遗漏、导航高亮误判、审计日志输出方式
- [x] 已新增生产配置骨架与部署文档
- [x] 已输出最终生产就绪总结：
  - `docs/PRODUCTION_READINESS_SUMMARY_20260312.md`

## 2. 仓库结构与职责（Repo Map）
- `apps/core/`：核心业务（订单、SKU、库存、转寄、设置、工作台、模板页面控制）
- `apps/api/`：API 聚合与对外 JSON 接口
- `templates/`：前端页面模板（订单、工作台、工作台首页、设置、排期等）
- `static/`：前端静态资源（CSS/JS）
- `scripts/`：辅助脚本（如验收报告自动生成）
- `docs/`：交付与验收文档（报告模板、历史报告）
- `start.bat` / `start.ps1`：一键启动脚本（含环境检查、可选验收流程）

## 3. 当前实现进度

### 3.0 工作台仓储联动（2026-03-12）
- [x] 仓储角色工作台新增卡片：`待质检回件`
- [x] 仓储角色工作台新增卡片：`待维修回件`
- [x] 仓储角色工作台新增卡片：`待执行维修单`
- [x] 卡片支持直达：
  - `回件质检池?status=pending`
  - `回件质检池?status=repair`
  - `维修工单?status=draft`
- [x] 已补自动化测试，确保仓储视图切换时统计和跳转口径正确
- [x] 工作台仓储角色新增“异常洞察”区：
  - 高频异常部件
  - 回件待处理焦点

### 3.0.1 仓储报表（2026-03-12）
- [x] 新增独立页面：`仓储报表`
- [x] 支持最近 `7/30` 天趋势查看
- [x] 覆盖维度：
  - 装配完成趋势
  - 新建维修单趋势
  - 单套处置完成趋势
  - 回件待质检新增趋势
  - 回件回库完成趋势
- [x] 支持状态分布查看：
  - 装配单
  - 维修工单
  - 单套处置
  - 回件质检
- [x] 支持导出 CSV
- [x] 工作台仓储角色快捷操作已增加 `仓储报表`
- [x] 新增部件层榜单：
  - 高频损耗部件
  - 维修替换排行
  - 回件质检结果排行

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
- [x] 转寄任务完成时，挂靠锁按 `source->target->sku` 精确消耗（支持部分数量拆分）
- [x] 前端提示“转寄+仓库”组合建议
- [x] 每条明细支持手选来源单（可选）并优先挂靠
- [x] 订单详情展示实际挂靠来源单与数量

### 3.5 列表/看板展示
- [x] 订单列表、工作台、工作台最近订单增加“转寄挂靠”展示
- [x] 工作台指标已接入仓库可用库存与转寄可用数量

### 3.6 测试与验收支持
- [x] `apps.core.tests` 已覆盖关键流程（含转寄锁与优先来源）
- [x] 验收报告模板与自动记录脚本已提供

### 3.7 SKU 部件组成（BOM）V1（2026-03-07）
- [x] 新增数据模型：`SKUComponent`（`sku_components`）
- [x] 产品管理支持在新建/编辑 SKU 时维护 BOM（部件 + 单套用量 + 备注）
- [x] SKU 列表新增“部件组成”列（项数 + 预览）
- [x] 新增自动化测试：`SKUBOMViewTests`
- [x] 全量回归通过：`74/74`

### 3.8 单套部件状态快照（2026-03-07）
- [x] 新增数据模型：`InventoryUnitPart`（单套-部件状态）
- [x] 当 SKU 初始化单套编号、SKU-BOM 变更时，自动同步单套部件快照
- [x] 在外库存明细新增“部件状态”列（正常/异常统计：缺件、损坏、丢失）
- [x] 在外库存 CSV 导出新增部件状态统计字段
- [x] 在外库存支持“单套部件盘点”弹窗保存（状态/实有数量/备注）
- [x] 盘点操作写入结构化审计日志（模块：在外库存）
- [x] 部件异常接入预警规则与健康分计算（影响风险排序）
- [x] 在外库存新增“仅部件异常”筛选
- [x] 全量回归：`82/82`

### 3.9 财务流水基础（2026-03-07）
- [x] 新增数据模型：`FinanceTransaction`（`finance_transactions`）
- [x] 订单确认时自动记账“收押金（deposit_received）”
- [x] 标记归还时自动记账“收尾款（balance_received）”
- [x] 订单完成自动记账“退押金（deposit_refund）”
- [x] 转寄任务完成导致来源单完成时，自动记账“退押金（deposit_refund）”
- [x] 订单详情新增“资金流水”表（支持按时间查看收款轨迹）
- [x] 新增 API：`/api/orders/<order_id>/finance-transactions/`
- [x] 新增自动化测试覆盖资金流水自动写入

### 3.10 数据一致性巡检（只读）（2026-03-07）
- [x] 新增巡检函数：`run_data_consistency_checks()`
- [x] 新增巡检命令：`python manage.py check_consistency --json`
- [x] 系统设置页（系统与界面TAB）新增“一键巡检”入口
- [x] 当前覆盖项：SKU库存与单套数量一致性、锁重复聚合、待执行转寄与锁数量匹配
- [x] 新增自动化测试覆盖巡检结果识别
- [x] 全量回归：`88/88`

### 3.11 风险事件 + 审批流V1（2026-03-07）
- [x] 新增风险事件模型：`RiskEvent`
- [x] 已接入自动风险事件：
  - 已发货订单重推后挂靠来源变更
  - 已履约订单取消
  - 24小时内高频取消
- [x] 新增风险事件处理台（筛选 + 关闭）
- [x] 新增审批任务模型：`ApprovalTask`
- [x] 新增审批中心页面（待审批/已执行/已驳回）
- [x] 高风险动作接入审批流：
  - `order.force_cancel`
  - `transfer.cancel_task`
- [x] 规则：无直权用户（manager/warehouse_manager）提交审批，admin/manager 审批执行，禁止自审

### 3.12 财务中心与巡检台账（2026-03-07）
- [x] 新增财务流水中心页面（筛选/分页/CSV导出）
- [x] 订单详情新增“手工记账”入口（扣罚/调整/退款等）
- [x] 新增一致性巡检执行台账：`DataConsistencyCheckRun`
- [x] 系统设置“一键巡检”自动保存台账
- [x] 巡检命令支持 `--save` 持久化执行结果

### 3.13 转寄决策回放（2026-03-07）
- [x] 新增推荐回放模型：`TransferRecommendationLog`
- [x] 转寄中心“重新推荐”动作自动记录推荐前/后来源与候选快照
- [x] 新增“转寄推荐回放”页面（筛选/分页）
- [x] 支持按订单/SKU/触发类型查询历史推荐轨迹
- [x] 全量回归：`100/100`

### 3.14 运维告警聚合中心（2026-03-08）
- [x] 运维中心页面升级：来源/级别筛选、CSV导出、四类明细表（超时转寄/超时审批/风险事件/巡检记录）
- [x] 运维中心告警聚合 API：`/api/dashboard/ops-alerts/`（支持筛选）
- [x] 新增自动化测试：`OpsCenterTests`、`api_ops_alerts` 用例

### 3.15 一致性修复工具（2026-03-08）
- [x] 新增修复计划函数：`build_data_consistency_repair_plan()`
- [x] 新增命令：`python manage.py repair_consistency [--apply] [--json] [--save]`
- [x] 默认 `dry-run`，仅 `--apply` 执行自动修复（当前支持 `legacy_stock_mismatch`）
- [x] 新增自动化测试：`ConsistencyRepairCommandTests`
- [x] 全量回归：`108/108`

### 3.16 运维定时任务入口（2026-03-08）
- [x] 新增命令：`python manage.py ops_watchdog [--json] [--source] [--severity] [--save-audit]`
- [x] 支持按来源/级别过滤输出告警，便于计划任务分级处理
- [x] 支持落审计日志（模块：运维中心，目标：`ops_watchdog`）
- [x] 新增自动化测试：`OpsWatchdogCommandTests`
- [x] 全量回归：`110/110`

### 3.17 审批SLA催办（2026-03-08）
- [x] 审批任务新增字段：`remind_count`、`last_reminded_at`
- [x] 审批中心支持“仅超时”筛选与单条“催办”按钮
- [x] 审批中心支持“批量催办超时审批”
- [x] 新增自动催办命令：`python manage.py approval_sla_remind [--hours] [--limit] [--dry-run] [--json]`
- [x] 催办动作写入审计日志（模块：审批）
- [x] 新增自动化测试：手工催办 + 命令催办
- [x] 全量回归：`114/114`

### 3.18 自动通知通道（2026-03-08）
- [x] 新增通知服务：`NotificationService`（Webhook + 审计兜底）
- [x] 系统设置新增通知配置：
  - `alert_notify_enabled`
  - `alert_notify_webhook_url`
  - `alert_notify_min_severity`
- [x] `ops_watchdog` 支持 `--notify`
- [x] `approval_sla_remind` 支持 `--notify`

### 3.19 修复工具扩展（2026-03-08）
- [x] `repair_consistency` 新增显式修复开关：`--fix-duplicate-locked`
- [x] 可选自动修复：同 source->target->sku 多条 `locked` 合并为一条（总量不变，冗余锁置 `released`）
- [x] 新增测试覆盖重复锁合并修复
- [x] 全量回归：`115/115`

### 3.20 财务差异归因与风控闭环（2026-03-08）
- [x] 财务对账中心新增“异常建议”列（押金/尾款/退押差异归因提示）
- [x] 财务对账异常支持一键“生成风险事件”
- [x] 新增测试覆盖：建议渲染、风险事件创建
- [x] 全量回归：`117/117`

### 3.21 上线运维包（2026-03-08）
- [x] 新增数据库备份脚本：`scripts/ops/backup_db.ps1`
- [x] 新增数据库恢复脚本：`scripts/ops/restore_db.ps1`
- [x] 新增巡检调度脚本：`scripts/ops/run_watchdog.bat`
- [x] 新增运维手册：`docs/OPS_RUNBOOK_20260308.md`

### 3.22 自动化冒烟命令（2026-03-08）
- [x] 新增命令：`python manage.py smoke_flow [--keep-data]`
- [x] 覆盖核心订单闭环（创建->确认->发货->归还->完成）
- [x] 默认自动清理冒烟数据，避免污染业务库
- [x] 新增测试：`SmokeFlowCommandTests`
- [x] 全量回归：`118/118`

### 3.23 分级审批能力（2026-03-08）
- [x] 审批任务新增字段：
  - `required_review_count`
  - `current_review_count`
  - `reviewed_user_ids`
  - `review_trail`
- [x] 审批通过支持“分段执行”：
  - 未达审批层级：仅记录审批进度，不执行动作
  - 达到审批层级：执行目标动作并置为已执行
- [x] 防重复审批：同一审批人不可重复审批同一任务
- [x] 系统设置新增审批层级配置：
  - `approval_required_count_order_force_cancel`
  - `approval_required_count_transfer_cancel_task`
- [x] 审批中心展示审批进度（x/y）
- [x] 新增自动化测试：二级审批流程与重复审批防护
- [x] 全量回归：`119/119`

### 3.24 角色看板切面预览与通知测试回归（2026-03-08）
- [x] 系统设置“测试通知”分支补充自动化测试（验证通知审计落库）
- [x] 工作台新增“角色视图”切换（仅 admin/manager 可切换，支持预览不同角色看板）
- [x] API `dashboard_role_view` 支持 `view_role` 切面参数（仅 admin/manager 生效）
- [x] 非管理角色传入 `view_role` 自动忽略，保持最小权限原则
- [x] 新增自动化测试：
  - 设置页通知测试分支
  - 工作台角色视图切换
  - API 角色切面 override/忽略
- [x] 全量回归：`123/123`

### 3.25 风险事件处理闭环增强（2026-03-08）
- [x] 风险事件新增字段：
  - `assignee`（负责人）
  - `processing_note`（处理备注）
- [x] 新增“认领处理中”动作：
  - 路由：`/risk-events/<id>/claim/`
  - 行为：`open -> processing`，自动记录负责人与处理备注
- [x] 风险事件列表页增强：
  - 展示负责人、处理备注
  - 非关闭事件支持“认领处理中 / 关闭”双动作
- [x] 关闭事件流程增强：
  - 若未分配负责人，关闭时自动将当前处理人设为负责人
  - 关闭备注写入 `processing_note`（结构化审计保留）
- [x] 新增迁移：`0014_riskevent_assignee_processing_note`
- [x] 新增自动化测试：
  - 认领成功（状态、负责人、备注、审计）
  - 已关闭事件不可认领
- [x] 全量回归：`125/125`

### 3.26 分页配置中心化（2026-03-09）
- [x] 系统设置新增分页配置项：
  - `page_size_default`（默认分页条数）
  - `page_size_transfer_candidates`（转寄候选分页）
  - `page_size_outbound_topology_units`（拓扑单套分页）
- [x] 视图层新增统一分页函数：`_get_page_size(...)`（1~100 安全边界）
- [x] 多页面分页改为读取配置（订单/转寄/在外库存/产品/采购/部件/风险/审批/财务/审计/用户）
- [x] 系统设置页新增“界面分页配置”可视化输入项
- [x] 新增自动化测试：
  - 订单列表读取 `page_size_default`
- [x] 全量回归：`126/126`

### 3.27 风险事件看板增强（2026-03-09）
- [x] 风险事件列表新增筛选：
  - 负责人筛选
  - 仅看我负责（`mine_only=1`）
- [x] 风险事件列表支持 CSV 导出（与当前筛选条件一致）
- [x] 页面体验增强：查询区新增“导出CSV”按钮
- [x] 新增自动化测试：
  - `mine_only` 过滤正确性
  - 导出仅包含筛选结果
- [x] 全量回归：`127/127`

### 3.28 审批中心筛选与导出增强（2026-03-09）
- [x] 审批中心新增筛选：
  - `mine_only=1`（仅我发起）
  - `reviewable_only=1`（待我审批）
- [x] 审批中心支持 CSV 导出（按当前筛选条件导出）
- [x] 页面新增“导出CSV”按钮
- [x] 新增自动化测试：
  - mine/reviewable 筛选正确性
  - 导出结果与筛选一致
- [x] 全量回归：`128/128`

### 3.29 财务对账导出信息增强（2026-03-09）
- [x] 财务对账 CSV 导出新增字段：
  - `差异摘要`（押金/尾款/退押维度）
  - `建议`（页面同口径建议文本）
  - `创建时间`
- [x] 导出内容与页面“建议”口径一致，便于线下对账复盘
- [x] 新增自动化测试：
  - 导出包含建议和差异摘要字段
- [x] 全量回归：`129/129`

### 3.30 风险事件 API 能力补齐（2026-03-09）
- [x] 新增 API：`GET /api/risk-events/`
- [x] 支持筛选参数：
  - `status`
  - `level`
  - `keyword`
  - `assignee`
  - `mine_only=1`
- [x] 返回扩展字段：
  - 级别/状态/类型显示值
  - `order_no`
  - `assignee_name`
  - `detected_by_name`
- [x] 返回统计 `meta`：
  - `total/open_count/processing_count/closed_count`
- [x] 新增 API 自动化测试：筛选正确性
- [x] 全量回归：`130/130`

### 3.31 审批任务 API 能力补齐（2026-03-09）
- [x] 新增 API：`GET /api/approvals/`
- [x] 支持筛选参数：
  - `status`
  - `action_code`
  - `keyword`
  - `overdue_only=1`
  - `mine_only=1`
  - `reviewable_only=1`
- [x] 返回扩展字段：
  - `status_display`
  - `requested_by_name`
  - `reviewed_by_name`
  - `review_progress`
- [x] 返回统计 `meta`：
  - `total/pending_count/executed_count/rejected_count/overdue_pending_count`
- [x] 新增 API 自动化测试：筛选正确性
- [x] 全量回归：`131/131`

### 3.32 财务对账 API 能力补齐（2026-03-09）
- [x] 新增 API：`GET /api/finance/reconciliation/`
- [x] 支持筛选参数：
  - `status`
  - `keyword`
  - `mismatch_only=1`
- [x] 返回字段与页面口径一致：
  - 押金/尾款/退押应收实收差异
  - `has_mismatch`
  - `suggestions`
- [x] 返回统计 `meta`：
  - `total`
  - `mismatch_count`
  - 当前筛选回显
- [x] 新增 API 自动化测试：异常筛选与建议文本
- [x] 全量回归：`132/132`

### 3.33 转寄推荐回放 API 能力补齐（2026-03-09）
- [x] 新增 API：`GET /api/transfers/recommendation-logs/`
- [x] 支持筛选参数：
  - `trigger_type`
  - `keyword`（订单号/客户/SKU）
- [x] 返回扩展字段：
  - `trigger_type_display`
  - `order_no/customer_name`
  - `sku_code/sku_name`
  - `operator_name`
- [x] 返回统计 `meta`：
  - `total`
  - 当前筛选回显
- [x] 新增 API 自动化测试：筛选正确性
- [x] 全量回归：`133/133`

### 3.34 转寄推荐回放导出增强（2026-03-09）
- [x] 转寄推荐回放页面新增 CSV 导出按钮（按当前筛选导出）
- [x] 导出字段包含：
  - 时间、目标订单/客户、SKU、触发类型
  - 推荐前来源、推荐后来源、候选数、仓库补量、操作人
- [x] 新增自动化测试：
  - 回放导出返回 CSV 且包含关键字段
- [x] 全量回归：`134/134`

### 3.35 转寄评分明细回放增强（2026-03-09）
- [x] 推荐日志持久化增强：
  - 候选排名 `score_rank`
  - 分项分值（时间差/可信度/距离）与总分 `score_total`
  - 决策说明 `decision_reason`
- [x] 系统设置新增评分权重：
  - `transfer_score_weight_date`
  - `transfer_score_weight_confidence`
  - `transfer_score_weight_distance`
- [x] 回放页新增“查看评分”折叠面板，可逐条查看候选评分明细与命中项
- [x] 回放 CSV 导出补充：
  - `命中排名`、`命中总分`、`决策说明`
- [x] 新增自动化测试：
  - 推荐写日志包含评分权重与候选总分
  - 页面包含评分回放入口
  - 导出包含新增评分列
- [x] 全量回归：`134/134`

### 3.36 转寄回放查询增强（2026-03-09）
- [x] 转寄推荐回放页面新增“决策类型”筛选：
  - `转寄命中`（命中来源单）
  - `仓库补量`（无来源单命中）
- [x] 转寄推荐回放 API 同步支持 `decision_type` 过滤参数
- [x] API 返回 `meta.decision_type`，便于前端回显与联调
- [x] 新增自动化测试：
  - 页面按决策类型筛选
  - API 按决策类型筛选
- [x] 全量回归：`135/135`

### 3.37 财务对账口径统一 + 巡检接入（2026-03-09）
- [x] 抽取统一函数：`build_finance_reconciliation_rows()`
  - 页面对账、API 对账、巡检三端共用同一口径
  - 输出统一字段：差异值、异常标记、建议、差异维度
- [x] 一致性巡检新增财务问题类型：
  - `finance_reconciliation_mismatch`
  - 巡检报告中可直接看到订单级财务差异与建议
- [x] API 与页面继续保持兼容字段输出（不破坏前端）
- [x] 新增自动化测试：
  - 巡检可识别财务差异问题
  - 财务对账页面/API回归通过
- [x] 全量回归：`136/136`

### 3.38 运维中心接入财务差异告警（2026-03-09）
- [x] 运维中心告警新增来源：`finance`
  - 告警项：`财务对账异常`
  - 数量口径：最近一次巡检中的 `finance_reconciliation_mismatch` 条数
- [x] 运维中心摘要新增指标：`财务差异订单`
- [x] 运维中心筛选新增“财务”来源选项
- [x] API `GET /api/ops/alerts/` 同步新增：
  - `summary.finance_mismatch_count`
  - `alerts` 中 `source=finance` 告警项
- [x] 新增自动化测试：
  - 运维中心页面展示财务告警
  - ops alerts API 返回财务告警与统计
- [x] 全量回归：`136/136`

### 3.39 审批层级策略增强（2026-03-09）
- [x] 审批配置新增：
  - `approval_required_count_default`（默认审批层级）
  - `approval_required_count_map`（按动作覆盖层级，JSON）
- [x] 审批申请创建逻辑增强：
  - 保持兼容原字段（订单强制取消/转寄任务取消）
  - 优先读取 `approval_required_count_map[action_code]`
  - 层级范围保护：`1..5`
- [x] 新增自动化测试：
  - 管理员发起订单强制取消时，按策略映射命中二级审批
- [x] 全量回归：`137/137`

### 3.40 审批策略配置校验（2026-03-09）
- [x] 系统设置保存时新增 `approval_required_count_map` 校验：
  - 必须是 JSON 对象
  - value 必须可转 int 且范围 `1..5`
- [x] 非法配置直接提示错误并拒绝保存（不污染已生效配置）
- [x] 新增自动化测试：非法 JSON/value 拒绝保存
- [x] 全量回归：`138/138`

### 3.41 一致性巡检结果结构增强（2026-03-09）
- [x] `run_data_consistency_checks()` 输出新增 `type_counts`（按问题类型统计）
- [x] 巡检结果持久化 `summary` 同步写入 `type_counts`
- [x] 新增自动化测试：财务差异问题类型计数正确
- [x] 全量回归：`138/138`

### 3.42 核心冒烟命令增强（2026-03-09）
- [x] `smoke_flow` 增强为“业务闭环 + 页面/API连通性检查”：
  - 页面：工作台/订单中心/转寄中心/转寄回放/审批/财务对账/运维中心
  - API：ops告警/风险事件/审批任务/财务对账/转寄回放
- [x] 新增参数：
  - `--skip-http-check`（仅跑业务闭环）
  - `--keep-data`（保留冒烟数据）
- [x] 修复冒烟命令测试环境登录稳定性（固定重设 `smoke_runner` 密码）
- [x] 新增自动化测试：
  - 默认模式包含连通性检查输出
  - `--skip-http-check` 模式可独立通过
- [x] 全量回归：`139/139`

### 3.43 运维巡检命令口径对齐（2026-03-09）
- [x] `ops_watchdog` 增强：
  - `--source` 支持 `finance`
  - `summary` 新增 `finance_mismatch_count`
  - `summary` 新增 `latest_check_type_counts`
  - 告警列表新增 `source=finance` 的“财务对账异常”
- [x] 与运维中心页面/API财务告警口径对齐
- [x] 新增自动化测试：
  - JSON输出包含财务差异统计与财务告警项
- [x] 全量回归：`139/139`

### 3.44 财务对账筛选增强（2026-03-09）
- [x] 财务对账页面新增筛选条件：
  - `mismatch_field`：押金/尾款/退押差异维度
  - `min_diff_amount`：最小差异金额阈值
- [x] 财务对账 API 同步支持：
  - `mismatch_field`
  - `min_diff_amount`
  - `meta` 回显新增上述字段
- [x] 对账口径复用函数已支持多维过滤（仍与页面/导出口径一致）
- [x] 新增自动化测试：
  - 页面按差异维度+金额阈值过滤
  - API 按差异维度+金额阈值过滤
- [x] 全量回归：`140/140`

### 3.45 巡检命令输出增强（2026-03-09）
- [x] `check_consistency` 文本输出新增“按类型统计”
- [x] 支持按问题类型快速查看数量（如 `legacy_stock_mismatch`、`finance_reconciliation_mismatch`）
- [x] 新增自动化测试：文本模式包含 `按类型统计` 与关键类型项
- [x] 全量回归：`141/141`

### 3.46 转寄回放详情 API（2026-03-09）
- [x] 新增接口：`GET /api/transfers/recommendation-logs/<log_id>/`
- [x] 返回单条回放完整信息（目标订单/SKU/候选快照/评分摘要/操作人）
- [x] 权限口径与回放列表一致（`transfers.view`）
- [x] 新增自动化测试：详情接口返回单条数据与评分摘要
- [x] 全量回归：`142/142`

### 3.47 角色看板指标钻取增强（2026-03-09）
- [x] 工作台 `focus_cards` 增加指标钻取信息（`url_name` + `query`）
- [x] 覆盖三类角色看板（经营/仓库/客服）关键卡片跳转
- [x] 页面渲染支持“卡片可点击跳转详情”，保留原统计展示逻辑
- [x] 新增自动化测试：
  - 页面用例校验卡片带跳转配置
  - API 用例校验角色看板返回卡片跳转字段
- [x] 全量回归：`143/143`

### 3.48 角色看板运营指标扩展（2026-03-09）
- [x] 统一统计口径新增：
  - `cancelled_orders`
  - `fulfillment_rate`（已完成/订单总数）
  - `cancel_rate`（已取消/订单总数）
  - `avg_transit_days`（已完成订单平均在途天数）
- [x] 角色看板新增 `kpi_entries`（履约率/取消率/平均在途天数）并支持跳转
- [x] 工作台页面新增运营指标展示区（只读，支持点击钻取）
- [x] 新增自动化测试：页面/API 均校验新指标字段
- [x] 全量回归：`143/143`

### 3.49 KPI趋势接口（2026-03-09）
- [x] 新增 API：`GET /api/dashboard/kpi-trend/?days=3..90`
- [x] 输出按日聚合的状态趋势桶（`pending/delivered/completed/cancelled`）
- [x] 返回 `meta`（时间范围、天数）用于前端图表组件直接接入
- [x] 新增自动化测试：按天输出长度、状态计数、参数边界
- [x] 全量回归：`144/144`

### 3.50 订单发货时效看板列（2026-03-09）
- [x] 订单中心新增发货时效能力：
  - 发货日期
  - 距离发货剩余天数
  - 时效状态（已发货/已超时/7天内预警/正常/待补发货日期）
- [x] 时效状态口径：
  - 已发货：`status in delivered/in_use/returned/completed` 或已有发货单号
  - 剩余天数：`ship_date - today`（已发货且已过期时按 `0` 展示）
- [x] 新增筛选：`发货时效`（overdue/warning/normal/shipped/unknown）
- [x] 默认排序按时效风险优先（超时 > 预警 > 正常 > 已发货 > 待补日期）
- [x] 新增自动化测试：
  - 时效计算与默认排序
  - 时效筛选
- [x] 全量回归：`146/146`

### 3.51 订单状态矩阵文档沉淀（2026-03-11）
- [x] 新增独立文档：`docs/ORDER_STATUS_MATRIX_20260311.md`
- [x] 明确状态主链路：
  - `待处理 -> 待发货 -> 已发货 -> 已归还 -> 已完成`
  - `待处理/待发货 -> 已取消`
- [x] 明确禁止跳转：
  - `已发货 -> 已取消`
  - `已发货 -> 已完成`
  - `终态 -> 任意其他状态`
- [x] 明确页面入口与后端动作权限联合约束
- [x] 纳入后续验收基准与培训基准

### 3.52 转寄任务状态矩阵文档沉淀（2026-03-11）
- [x] 新增独立文档：`docs/TRANSFER_STATUS_MATRIX_20260311.md`
- [x] 明确三层对象：
  - `TransferAllocation`（挂靠锁）
  - `Transfer`（转寄任务）
  - `UnitMovement`（单套链路）
- [x] 明确任务主链路：
  - `pending -> completed`
  - `pending -> cancelled`
- [x] 明确候选重推、生成任务、完成任务、取消任务的前置条件
- [x] 明确转寄任务完成后对来源单/目标单/挂靠锁/单套链路的联动
- [x] 纳入后续转寄功能开发与验收基准

### 3.53 单套库存节点矩阵文档沉淀（2026-03-11）
- [x] 新增独立文档：`docs/INVENTORY_UNIT_NODE_MATRIX_20260311.md`
- [x] 明确单套对象：
  - `InventoryUnit`
  - `UnitMovement`
- [x] 明确单套状态：
  - `in_warehouse / in_transit / maintenance / scrapped`
- [x] 明确单套节点：
  - `WAREHOUSE_OUT / TRANSFER_PENDING / TRANSFER_SHIPPED / TRANSFER_COMPLETED / RETURNED_WAREHOUSE / EXCEPTION`
- [x] 明确单套主链路、异常节点、统计口径、验收清单
- [x] 纳入后续在外库存看板与链路拓扑开发基准

### 3.54 订单/转寄/单套库存三级联动总图文档（2026-03-11）
- [x] 新增独立文档：`docs/ORDER_TRANSFER_UNIT_LINKAGE_OVERVIEW_20260311.md`
- [x] 明确三层对象职责边界：
  - `Order`
  - `Transfer / TransferAllocation`
  - `InventoryUnit / UnitMovement`
- [x] 明确三层驱动关系与典型闭环场景
- [x] 明确三层一致性约束与运营常见问题映射
- [x] 作为后续培训、巡检、规则收敛的总纲文档

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
2. 财务流水已覆盖基础交易与手工记账，但仍缺“对账差异自动定位与纠偏策略”。
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
- `python manage.py check_consistency --json`
- `python manage.py test apps.core.tests -v 1`
- `python manage.py test apps.api.tests -v 1`

## 7. 下一步开发建议（建议顺序）
1. 转寄决策回放：补齐“每次推荐评分明细持久化 + 查询回放”。
2. 财务对账中心：四账一致性校验与差异定位。
3. 审批流增强：二级审批、字段级审批策略、审批SLA预警。
4. 自动化验收：补充关键页面 E2E 冒烟脚本。
5. 监控告警：巡检定时任务与运维告警聚合视图。

## 8. 新会话快速上手清单（给未来自己）
1. 先读本文件 + `README.md` + `README_PHASE2.md` + `DELIVERY_REPORT.md`
2. 跑 `python manage.py check` 与 `apps.core.tests`
3. 进页面优先验：新建订单 -> 转寄选择/自动匹配 -> 列表挂靠显示 -> 详情挂靠
4. 若有问题，先查：`apps/core/utils.py`、`apps/core/services/order_service.py`、`apps/core/views.py`
5. 涉及订单状态流转时，先对照：`docs/ORDER_STATUS_MATRIX_20260311.md`
6. 涉及转寄候选/任务/挂靠锁/单套链路时，先对照：`docs/TRANSFER_STATUS_MATRIX_20260311.md`
7. 涉及单套库存、在外库存、链路拓扑时，先对照：`docs/INVENTORY_UNIT_NODE_MATRIX_20260311.md`
8. 需要从全局理解三层联动时，先看：`docs/ORDER_TRANSFER_UNIT_LINKAGE_OVERVIEW_20260311.md`
9. 涉及 SKU 装配驱动库存、BOM 扣减、维修换件工单时，先看：`docs/SKU_ASSEMBLY_MAINTENANCE_PLAN_20260311.md`

## 9. 2026-03-11 新增进展：装配驱动库存第三阶段完成

### 已完成
- 新增 `AssemblyOrder / AssemblyOrderItem`：SKU 新增库存必须通过装配单完成。
- 新增 `MaintenanceWorkOrder / MaintenanceWorkOrderItem`：单套部件折损通过维修工单换件。
- 新增 `AssemblyService`：执行装配时扣减部件库存、生成单套编号、同步 SKU 库存。
- 新增 `MaintenanceService`：创建工单时单套进入维修中，执行工单时扣减替换部件并更新单套部件快照。
- 改造 `InventoryUnitService`：新增 `create_units_for_sku`、`refresh_sku_stock`。
- 改造产品管理：
  - 新建 SKU 不再直接录入库存
  - 编辑 SKU 不再允许直接修改库存
  - 新增“新增库存”装配入口
- 改造在外库存看板：
  - 新增“维修换件”入口
  - 新增最近维修工单列表
- 新增单套处置闭环：
  - `UnitDisposalOrder / UnitDisposalOrderItem`
  - 支持 `拆解回件` / `报废停用`
  - 拆解回件会按单套实有部件生成待质检回件记录
  - 报废停用会直接停用单套，不回收入库
- 补齐回滚/取消：
  - 装配单支持安全取消并回补部件库存
  - 维修工单支持草稿取消并恢复单套状态
- `InventoryUnit` 新增 `source_assembly_order`，装配来源可追溯
- 单套处置接入审批中心：
  - 新动作码 `unit.dispose`
  - 无直接权限时走审批申请
  - 审批通过后执行拆解/报废
- 新增独立页面：
  - `装配单`
  - `部件折损池`
  - `维修工单`
  - `单套处置单`
- 维修工单新增“反向冲销”闭环：
  - 已完成维修工单支持冲销
  - 自动回补替换新部件库存
  - 自动回退单套部件快照
  - 工单状态改为 `reversed`
- SKU 库存口径开始切换到“单套聚合优先”：
  - `SKU.effective_stock` 生效
  - 订单表单、产品管理列表、SKU详情接口优先展示聚合库存
  - 可用库存计算与排期统计优先使用聚合口径
  - `SKU.stock` 降级为“历史兼容镜像字段”，业务主流程不再依赖
- 单套处置审批支持细分阈值：
  - `approval_required_count_unit_disassemble`
  - `approval_required_count_unit_scrap`
  - `approval_required_count_map` 支持 `unit.dispose.disassemble / unit.dispose.scrap`
- 三类仓储单据补齐导出能力：
  - 装配单支持按 `keyword/sku_id/status` 导出 CSV
  - 维修工单支持按 `keyword/status` 导出 CSV
  - 单套处置单支持按 `keyword/action_type/status` 导出 CSV
  - 页面新增“导出CSV”按钮，导出口径与当前筛选一致
- 新增“回件质检池”闭环：
  - `PartRecoveryInspection`
  - 单套拆解后生成 `pending` 质检记录，不再直接回补库存
  - 支持三种处理结果：`returned / repair / scrapped`
  - `returned` 时才真正回补部件库存
  - 页面入口：`回件质检池`
  - `repair` 状态支持二段流转：
    - `维修完成回库`
    - `维修失败报废`
- 仓储单据页补齐导出与统计展示：
  - 回件质检池支持按 `keyword/status` 导出 CSV
  - 装配单页新增：总单数 / 已完成 / 已取消 / 累计装配套数
  - 维修工单页新增：总工单 / 待执行 / 已完成 / 已冲销
  - 单套处置单页新增：总单数 / 拆解回件 / 报废停用 / 已完成

### 本轮验证
- `python manage.py migrate` 通过
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`151/151`）

---
如需继续开发，优先从“装配/维修/处置多维趋势报表”和“仓储看板联动工作台”两项继续。

## 10. 2026-03-12 新增进展：仓储检索口径统一完成

### 已完成
- 仓储工作台已增加：
  - 待质检回件
  - 待维修回件
  - 待执行维修单
  - 高频异常部件
  - 回件待处理焦点
- 新增 `仓储报表` 页面及导出能力：
  - 汇总指标
  - 7/30 天趋势
  - 装配/维修/处置/回件状态分布
  - 高频损耗部件
  - 维修替换排行
  - 回件质检结果排行
- 仓储相关页面的关键词搜索范围已统一收口：
  - `装配单`：装配单号 / SKU / 部件 / 创建人 / 备注
  - `维修工单`：工单号 / 单套号 / SKU / 部件 / 订单号 / 问题描述
  - `单套处置单`：工单号 / 单套号 / SKU / 部件 / 原因说明 / 创建人 / 订单号（有值时）
  - `回件质检池`：处置单 / 单套 / SKU / 部件 / 备注 / 订单号（有值时）
  - `部件折损池`：单套号 / SKU / 部件 / 订单号 / 备注
- 各仓储页面的输入占位文案已与实际搜索能力保持一致，避免前端提示与后端实现脱节。
- 仓储报表支持多维筛选：
  - `统计周期`
  - `SKU`
  - `部件`
  - 页面展示、趋势、状态分布、榜单、CSV 导出均使用同一筛选口径
  - 导出文件会写入当前筛选条件，便于线下追溯报表来源

### 本轮验证
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`159/159`）

## 11. 2026-03-12 新增进展：工作台与仓储报表下钻联动

### 已完成
- 工作台 `高频异常部件`、`回件待处理焦点` 主链接改为直达 `仓储报表`，并自动带 `part_id`
- 工作台每条仓储洞察增加 `明细` 次级入口，可直接跳到原始明细列表
- `仓储报表` 三个部件榜单已支持下钻：
  - 高频损耗部件 -> `部件折损池`
  - 维修替换排行 -> `维修工单`
  - 回件质检结果排行 -> `回件质检池`
- `仓储报表 -> 明细页` 已支持带筛选口径跳转：
  - `维修工单` 支持 `sku_id / part_id`
  - `部件折损池` 支持 `sku_id / part_id`
  - `回件质检池` 支持 `sku_id / part_id`
  - 明细页前端同步增加 `SKU / 部件` 下拉，避免只靠隐藏 URL 参数

### 本轮验证
- 已补联动测试
- 明细页已新增“来自仓储报表下钻”的来源提示与返回链路

## 12. 2026-03-12 新增进展：工作台仓储库存口径修正

### 已完成
- `get_dashboard_stats_payload` 的仓储总库存统计已从旧 `SKU.stock` 汇总切换为：
  - `InventoryUnit` 激活单套数
  - 排除 `scrapped`
- 避免工作台和角色看板继续受到历史兼容字段漂移影响
- 已新增自动化测试，锁定“工作台库存必须使用单套口径”

### 本轮验证
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`164/164`）

## 13. 2026-03-12 新增进展：订单详情页权限显示与状态矩阵对齐

### 已完成
- 订单详情页 `returned -> 标记完成` 已增加动作权限判断
- 避免无 `order.mark_returned` 权限的角色在详情页看到可操作按钮
- 页面显示规则与后端状态机、订单列表页保持一致

### 本轮验证
- 新增详情页权限测试
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`165/165`）

## 14. 2026-03-12 新增进展：转寄候选任务状态展示修正

### 已完成
- `build_transfer_pool_rows()` 细分记录 `task_status`
- 转寄候选页状态徽章调整为：
  - `pending` -> `已生成任务`
  - `completed` -> `已完成任务`
  - 无任务且当前来源为仓库 -> `仓库挂靠`
  - 无任务且当前来源为转寄 -> `转寄挂靠`
- 保持原有业务约束不变：
  - 存在 `pending/completed` 任务时，仍不可重推
  - 存在 `pending/completed` 任务时，仍不可再次生成任务
- 补充测试，锁定 `completed` 任务不再错误显示成“已生成任务”

### 本轮验证
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`165/165`）

## 15. 2026-03-12 新增进展：订单列表页完成按钮权限显示收口

### 已完成
- 订单列表页 `已归还 -> 标记完成` 已增加动作权限判断
- 现在与订单详情页保持一致：
  - 仅有 `order.mark_returned` 权限的用户才会看到 `标记完成`
- 避免列表页继续出现“前端可见、后端会拦”的误导按钮

### 本轮验证
- 新增订单列表页权限测试
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`166/166`）

## 16. 2026-03-12 新增进展：订单中心状态筛选补齐已归还

### 已完成
- 订单中心状态筛选增加 `已归还(returned)`
- 现在列表页可以直接筛出待闭环的归还订单，便于财务和运营跟进
- 补充筛选测试，锁定 `returned` 选项和列表结果

### 本轮验证
- 新增订单列表 `returned` 筛选测试
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`167/167`）

## 17. 2026-03-12 新增进展：已归还状态展示统一

### 已完成
- 订单中心列表页为 `returned` 增加专门状态徽章：`已归还`
- 工作台“最近订单”同样为 `returned` 增加专门状态徽章
- 避免 `已归还` 继续落入默认样式，提升待闭环订单识别度

### 本轮验证
- 新增订单中心/工作台 `returned` 状态展示测试
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`168/168`）

## 18. 2026-03-12 新增进展：转寄中心完成任务文案误导修正

### 已完成
- 转寄候选里已完成任务的禁用提示不再显示“待执行任务”
- `重新推荐` 的跳过提示改为：
  - `已存在转寄任务（待执行或已完成），不可重推`
- `生成任务` 的跳过提示改为：
  - `已存在转寄任务（待执行或已完成）`
- 避免已完成任务继续被误解为“还没执行”

### 本轮验证
- 补充 completed 任务下的重推/生成提示测试
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`169/169`）

## 19. 2026-03-12 新增进展：订单详情页补充来源单占用说明

### 已完成
- 订单详情页“履约信息”新增 `来源单占用`
- 当订单已发货但不能在订单中心直接归还时：
  - `转寄中心操作` 按钮会显示 `（来源占用中）`
  - 页面补充说明文案，明确该订单正作为来源单被占用，需要前往转寄中心完成闭环
- 解决仓库发货但后续被挂靠为来源单时，详情页缺少解释信息的问题

### 本轮验证
- 新增来源单占用详情页展示测试
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`169/169`）

## 20. 2026-03-12 新增进展：转寄候选按钮禁用原因明确化

### 已完成
- `build_transfer_pool_rows()` 新增：
  - `can_recommend_reason`
  - `can_generate_reason`
- 转寄候选页中：
  - 禁用的复选框会明确显示原因
  - 禁用的 `重新推荐`
  - 禁用的 `生成任务`
    现在也会带具体 title，说明是：
    - 已有待执行任务
    - 已有已完成任务
    - 目标订单未发货
    - 当前推荐仍为仓库发货
- 降低操作员对“按钮为什么不能点”的猜测成本

### 本轮验证
- 新增转寄候选禁用原因测试
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`170/170`）

## 21. 2026-03-12 新增进展：发货/归还单号改为后端强校验

### 已完成
- `OrderService.mark_as_delivered()` 新增快递单号非空校验
- `OrderService.mark_as_returned()` 新增回收单号非空校验
- 订单详情页对应输入框改为：
  - `发货单号` 必填
  - `回收单号` 必填
- 收口前后端口径，避免页面写“可选”但后端实际又依赖单号的情况

### 本轮验证
- 新增发货/归还单号必填测试
- `python manage.py check` 通过
- `python manage.py test apps.core.tests -v 1` 通过（`172/172`）
### 2026-03-12 新增进展：订单中心关键词检索补齐微信号与闲鱼单号

- 订单中心关键词检索已纳入：
  - `customer_wechat`
  - `xianyu_order_no`
- 订单列表页搜索框占位文案同步更新为：
  - `订单号/客户/电话/微信号/闲鱼单号`
- 新增自动化测试覆盖：
  - 按微信号搜索订单
  - 按闲鱼订单号搜索订单
- 验证结果：
  - `python manage.py check`
  - `python manage.py test apps.core.tests -v 1`
  - 全量通过：`173/173`
### 2026-03-12 新增进展：采购与部件导航高亮规则收口

- 左侧导航中以下菜单已从“路径字符串匹配”改为“`url_name` 精确匹配”：
  - `采购单`
  - `部件库存`
  - `部件流水`
- 采购单相关创建/编辑/状态流转页面会稳定高亮 `采购单`
- 不再出现 `采购单` 页面误联动高亮 `订单中心` 的情况
- 新增自动化测试覆盖：
  - 进入 `采购单` 页面时，只高亮 `采购单` 菜单，不高亮 `订单中心`
- 验证结果：
  - `python manage.py check`
  - `python manage.py test apps.core.tests -v 1`
  - 全量通过：`174/174`
### 2026-03-12 新增进展：生产配置骨架与发布文档补齐

- 新增共享配置文件：
  - `config/settings_common.py`
- 新增开发配置入口：
  - `config/settings_dev.py`
- 新增生产配置入口：
  - `config/settings_prod.py`
- 保留 `config/settings.py` 作为兼容默认入口，避免影响当前开发启动
- 新增生产环境变量模板：
  - `.env.prod.example`
- 新增生产部署文档：
  - `docs/PRODUCTION_DEPLOYMENT_GUIDE_20260312.md`
  - `docs/PRODUCTION_RELEASE_CHECKLIST_20260312.md`
- 审计中间件失败日志已改为标准 logger，不再直接 `print`
- 验证结果：
  - 默认 `config.settings` 可正常执行 `manage.py check`
  - `config.settings_prod` 在提供完整环境变量时可正常导入
