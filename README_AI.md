# README_AI.md
## 项目是做什么的
本项目是一个面向“宝宝周岁宴道具租赁”场景的业务系统，核心目标是把周岁宴套餐/道具的租赁、发货、转寄、归还、装配、维修、部件库存、财务与审批流程放到同一套系统内管理。

从当前代码看，系统不是简单的订单录入工具，而是已经扩展到了“订单流转 + 转寄任务 + 单套库存 + BOM 装配 + 仓储维修处置 + 运营风控”的一体化后台。

## 技术栈
根据当前代码实际情况，项目技术栈如下：

- 后端框架：Django 4.2.9
- API：Django REST framework 3.14.0
- 应用形态：Django 单体应用（服务端模板渲染为主，辅以部分 API）
- 前端：Django Templates + 原生 JavaScript + Bootstrap 风格主题样式
- WSGI 服务：Gunicorn（生产依赖已配置）
- 静态资源：WhiteNoise
- 数据库：
  - 开发默认支持 SQLite
  - 生产配置支持 PostgreSQL（`psycopg2-binary`）
- 其他依赖：python-dateutil
- 运行方式：
  - Windows 启动脚本：`start.bat`、`start.ps1`
  - Shell 启动脚本：`start.sh`
  - 生产脚本：`scripts/start_prod.sh`
  - Docker 生产样板：`docker-compose.prod.yml`

## 目录结构
基于当前仓库代码，核心目录结构如下：

```text
zhousuiyan-system/
├─ apps/
│  ├─ core/                     # 核心业务：模型、视图、服务、权限、测试
│  │  ├─ management/commands/   # 巡检、修复、验收、提醒等命令
│  │  └─ services/              # 订单、库存、装配、审批、风控等服务层
│  └─ api/                      # API 序列化与接口
├─ config/                      # Django 配置（common/dev/prod）
├─ templates/                   # 页面模板
│  ├─ orders/                   # 订单列表/表单/详情
│  └─ procurement/              # 采购、部件、装配、维修、处置、报表
├─ static/                      # CSS / JS / 图片 / 第三方前端资源
├─ media/                       # 用户上传文件（如 SKU 图片）
├─ docs/                        # 项目进度、状态矩阵、生产部署等文档
├─ deploy/                      # Nginx 等部署样板
├─ scripts/                     # 初始化、启动、验收等脚本
├─ manage.py
├─ requirements.txt
└─ README.md
```

## 主要模块
### 1. 订单中心
核心文件：
- `apps/core/models.py`
- `apps/core/services/order_service.py`
- `templates/orders/list.html`
- `templates/orders/form.html`
- `templates/orders/detail.html`

当前支持：
- 新建/编辑/查看订单
- 微信号、闲鱼订单号等客户信息维护
- 发货日期推算与时效判断
- 发货、归还、完成、取消等状态流转
- 列表筛选、关键词查询、分页、导出类操作基础

### 2. 转寄中心
核心文件：
- `templates/transfers.html`
- `apps/core/utils.py`
- `apps/core/views.py`

当前支持：
- 转寄候选池
- 当前挂靠 / 推荐来源展示
- 转寄任务生成、完成、取消
- 候选重推、转寄来源推荐
- 转寄任务状态分栏与历史说明

### 3. 在外库存 / 单套库存看板
核心文件：
- `templates/outbound_inventory.html`
- `apps/core/services/inventory_unit_service.py`

当前支持：
- 单套编号管理
- 单套流转节点追踪
- 单套与订单、转寄、维修、处置的关联
- 在外库存可视化基础能力

### 4. SKU / BOM / 装配
核心文件：
- `templates/skus.html`
- `apps/core/services/assembly_service.py`
- `apps/core/models.py`

当前支持：
- SKU 基础资料维护
- BOM（部件组成）配置
- 通过装配单新增库存
- 装配时扣减部件库存
- 自动生成单套编号与单套部件快照

### 5. 维修、处置、回件质检
核心文件：
- `templates/procurement/maintenance_work_orders.html`
- `templates/procurement/unit_disposal_orders.html`
- `templates/procurement/part_issue_pool.html`
- `templates/procurement/part_recovery_inspections.html`

当前支持：
- 维修工单
- 单套拆解/报废处置
- 部件折损池
- 回件质检池
- 回件回库 / 转待维修 / 报废的二段处理

### 6. 采购与部件库存
核心文件：
- `templates/procurement/purchase_orders.html`
- `templates/procurement/parts_inventory.html`
- `templates/procurement/parts_movements.html`
- `apps/core/services/procurement_service.py`

当前支持：
- 采购单
- 部件库存
- 部件流水
- 与 BOM / 装配 / 维修的联动

### 7. 财务、审批、风控、审计、运维
核心文件：
- `templates/finance_transactions.html`
- `templates/finance_reconciliation.html`
- `templates/approvals.html`
- `templates/risk_events.html`
- `templates/audit_logs.html`
- `templates/ops_center.html`

当前支持：
- 财务流水与对账基础页面
- 审批中心
- 风险事件管理
- 审计日志
- 运维中心与部分管理命令

### 8. 工作台 / 角色看板 / 仓储报表
核心文件：
- `templates/dashboard.html`
- `templates/procurement/warehouse_reports.html`
- `static/js/workbench.js`

当前支持：
- 工作台卡片
- 角色视图切换
- KPI 统计
- 仓储待办卡片
- 仓储报表、趋势、榜单、导出

## 当前开发状态
从当前代码和文档看，项目已经不处于“起步阶段”，而是处于：

- 核心模块基本落地
- 主业务流转已基本跑通
- 状态机、权限、库存口径已做过多轮收口
- 正在持续做：
  - 页面交互优化
  - 状态闭环校验
  - 生产部署准备
  - UI 一致性与稳定性修复

可以概括为：
- **已完成：核心业务骨架和主流程**
- **进行中：细节体验、异常边界、生产上线准备**

## 推荐阅读顺序
如果后续继续开发，建议按这个顺序快速理解项目：

1. `docs/PROJECT_PROGRESS.md`
2. `docs/ORDER_STATUS_MATRIX_20260311.md`
3. `docs/TRANSFER_STATUS_MATRIX_20260311.md`
4. `docs/INVENTORY_UNIT_NODE_MATRIX_20260311.md`
5. `docs/ORDER_TRANSFER_UNIT_LINKAGE_OVERVIEW_20260311.md`
6. `apps/core/models.py`
7. `apps/core/services/`
8. `templates/orders/`、`templates/procurement/`、`templates/transfers.html`
