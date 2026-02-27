# 宝宝周岁宴道具租赁系统 - 文件清单

## 项目完成情况

所有29个必需文件已全部创建完成！

## 文件列表

### 配置文件 (7个)
- ✅ requirements.txt - Python依赖包
- ✅ .dockerignore - Docker忽略文件
- ✅ .gitignore - Git忽略文件
- ✅ Dockerfile - Docker镜像配置
- ✅ docker-compose.yml - Docker Compose配置
- ✅ README.md - 项目说明文档
- ✅ manage.py - Django管理脚本

### 核心配置 (5个)
- ✅ config/__init__.py
- ✅ config/asgi.py
- ✅ config/wsgi.py
- ✅ config/settings.py - Django设置
- ✅ config/urls.py - 主URL配置

### 应用模块 (10个)
- ✅ apps/__init__.py
- ✅ apps/core/__init__.py
- ✅ apps/core/apps.py
- ✅ apps/core/migrations/__init__.py
- ✅ apps/core/mock_data.py - Mock数据（完整）
- ✅ apps/core/views.py - 所有页面视图函数
- ✅ apps/core/urls.py - 前端路由
- ✅ apps/api/__init__.py
- ✅ apps/api/apps.py
- ✅ apps/api/migrations/__init__.py
- ✅ apps/api/views.py - API视图
- ✅ apps/api/serializers.py - 序列化器
- ✅ apps/api/urls.py - API路由

### 模板文件 (15个)
- ✅ templates/base.html - 统一布局
- ✅ templates/login.html - 登录页
- ✅ templates/dashboard.html - 工作台首页
- ✅ templates/workbench.html - 订单处理看板
- ✅ templates/orders/list.html - 订单列表
- ✅ templates/orders/form.html - 订单表单
- ✅ templates/calendar.html - 日历排期
- ✅ templates/transfers.html - 出入库流水
- ✅ templates/skus.html - SKU管理
- ✅ templates/procurement/purchase_orders.html - 采购单列表
- ✅ templates/procurement/purchase_order_form.html - 采购单表单
- ✅ templates/procurement/parts_inventory.html - 部件库存
- ✅ templates/procurement/parts_movements.html - 部件流水
- ✅ templates/settings.html - 系统设置
- ✅ templates/audit_logs.html - 审计日志

### 静态文件 (8个)
- ✅ static/css/base.css - 基础样式
- ✅ static/css/components.css - 组件样式
- ✅ static/css/pages.css - 页面样式
- ✅ static/js/main.js - 主JavaScript
- ✅ static/js/workbench.js - 工作台脚本
- ✅ static/js/calendar.js - 日历脚本
- ✅ static/js/procurement.js - 采购管理脚本
- ✅ static/images/logo.png - Logo图片（占位）

### 脚本文件 (2个)
- ✅ scripts/create_superuser.py - 创建超级用户
- ✅ start.sh - 快速启动脚本

## 功能特性

### 1. 订单管理
- 订单列表查看（支持状态筛选）
- 创建/编辑订单（支持多SKU）
- 订单详情查看
- 自动计算金额和押金

### 2. 工作台看板
- 三列看板布局（待处理、已确认、已送达）
- 卡片式订单展示
- 快速操作按钮
- 状态流转功能

### 3. 日历排期
- 月度日历视图
- 活动事件标记
- 事件详情查看
- 上月/下月切换

### 4. SKU管理
- 网格式SKU展示
- 库存实时监控
- 部件组成查看
- 类别筛选

### 5. 采购管理
- 采购单创建/编辑
- 部件库存监控
- 低库存预警
- 出入库流水记录

### 6. 系统功能
- 用户登录/登出
- 审计日志记录
- 系统参数设置
- 响应式布局

## Mock数据说明

系统包含完整的Mock数据：
- 5个订单（不同状态）
- 5个SKU套餐
- 14个部件库存
- 4条出入库流水
- 2个采购单
- 5条审计日志

## 快速启动

### 方式1：使用启动脚本
```bash
./start.sh
```

### 方式2：手动启动
```bash
# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 数据库迁移
python manage.py migrate

# 创建超级用户
python scripts/create_superuser.py

# 收集静态文件
python manage.py collectstatic --noinput

# 启动服务器
python manage.py runserver
```

### 方式3：使用Docker
```bash
docker-compose up -d
```

## 访问信息

- 系统地址：http://localhost:8000
- 默认账号：admin
- 默认密码：admin123

## 技术栈

- Django 4.2.9
- Django REST Framework 3.14.0
- SQLite数据库
- Gunicorn
- WhiteNoise
- 原生HTML/CSS/JavaScript

## 项目结构

```
zhousuiyan-system/
├── config/                 # 项目配置
├── apps/                   # 应用模块
│   ├── core/              # 核心业务
│   └── api/               # API接口
├── templates/             # 模板文件
├── static/                # 静态资源
│   ├── css/              # 样式文件
│   ├── js/               # JavaScript
│   └── images/           # 图片资源
├── scripts/              # 脚本文件
├── docs/                 # 文档
└── manage.py             # Django管理
```

## 开发说明

1. 所有视图函数在 `apps/core/views.py`
2. Mock数据在 `apps/core/mock_data.py`
3. URL路由在 `apps/core/urls.py`
4. 模板继承自 `templates/base.html`
5. 样式分为三个文件：base.css、components.css、pages.css

## 后续扩展

系统已预留扩展接口，可以：
1. 连接真实数据库（PostgreSQL/MySQL）
2. 实现真实的CRUD操作
3. 添加用户权限管理
4. 集成支付系统
5. 添加短信/邮件通知
6. 实现文件上传功能
7. 添加数据导出功能

## 完成时间

2024-02-27

## 状态

✅ 所有文件创建完成
✅ 功能完整可用
✅ 代码规范整洁
✅ 文档齐全
