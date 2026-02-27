# 宝宝周岁宴道具租赁系统

一个完整的宝宝周岁宴道具租赁管理系统，基于Django开发。

## 功能特性

### 核心功能
- **订单管理**：创建、编辑、查看订单，支持多SKU组合
- **工作台看板**：可视化订单处理流程（待处理、已确认、已送达）
- **日历排期**：直观查看活动排期，避免冲突
- **SKU管理**：套餐和单品管理，支持库存追踪
- **出入库管理**：完整的出入库流水记录

### 采购管理
- **采购单管理**：创建和管理采购订单
- **部件库存**：实时库存监控，低库存预警
- **部件流水**：详细的部件出入库记录

### 系统功能
- **用户认证**：安全的登录系统
- **审计日志**：完整的操作记录
- **系统设置**：灵活的业务参数配置

## 技术栈

- **后端框架**：Django 4.2.9
- **REST API**：Django REST Framework 3.14.0
- **数据库**：SQLite（可切换到PostgreSQL/MySQL）
- **Web服务器**：Gunicorn
- **静态文件**：WhiteNoise
- **前端**：原生HTML/CSS/JavaScript

## 快速开始

### 环境要求
- Python 3.11+
- pip

### 环境变量（建议）
可在项目根目录创建 `.env`（或直接设置系统环境变量）：

```bash
SECRET_KEY=replace-with-your-secret-key
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost
```

### 安装步骤

1. 克隆项目
```bash
git clone <repository-url>
cd zhousuiyan-system
```

2. 创建虚拟环境
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows
```

3. 安装依赖
```bash
pip install -r requirements.txt
```

4. 数据库迁移
```bash
python manage.py migrate
```

5. 创建超级用户
```bash
python scripts/create_superuser.py
```

6. 收集静态文件
```bash
python manage.py collectstatic --noinput
```

7. 运行开发服务器
```bash
python manage.py runserver
```

8. 访问系统
- 前端：http://localhost:8000
- 管理后台：http://localhost:8000/admin
- 默认账号：admin / admin123

### 一键启动（Windows）
在项目根目录运行：

```powershell
.\start.bat
```

可仅做环境准备不启动服务：

```powershell
.\start.bat -NoRunServer
```

## Docker部署

### 使用Docker Compose

1. 构建并启动
```bash
docker-compose up -d
```

2. 访问系统
- 系统地址：http://localhost:8000
- 默认账号：admin / admin123

### 单独使用Docker

1. 构建镜像
```bash
docker build -t zhousuiyan-system .
```

2. 运行容器
```bash
docker run -d -p 8000:8000 zhousuiyan-system
```

## 项目结构

```
zhousuiyan-system/
├── config/                 # 项目配置
│   ├── settings.py        # Django设置
│   ├── urls.py            # 主URL配置
│   ├── wsgi.py            # WSGI配置
│   └── asgi.py            # ASGI配置
├── apps/                   # 应用模块
│   ├── core/              # 核心业务模块
│   │   ├── views.py       # 视图函数
│   │   ├── urls.py        # URL路由
│   │   └── mock_data.py   # Mock数据
│   └── api/               # API模块
│       ├── views.py       # API视图
│       ├── urls.py        # API路由
│       └── serializers.py # 序列化器
├── templates/             # 模板文件
│   ├── base.html         # 基础模板
│   ├── login.html        # 登录页
│   ├── dashboard.html    # 工作台
│   ├── workbench.html    # 订单处理
│   ├── orders/           # 订单相关模板
│   ├── procurement/      # 采购相关模板
│   └── ...
├── static/               # 静态文件
│   ├── css/             # 样式文件
│   ├── js/              # JavaScript文件
│   └── images/          # 图片资源
├── scripts/             # 脚本文件
│   └── create_superuser.py
├── docs/                # 文档
├── manage.py            # Django管理脚本
├── requirements.txt     # Python依赖
├── Dockerfile          # Docker配置
├── docker-compose.yml  # Docker Compose配置
└── README.md           # 项目说明
```

## 主要页面

### 1. 工作台（Dashboard）
- 订单统计卡片
- 快捷操作入口
- 最近订单列表
- 低库存预警

### 2. 订单处理（Workbench）
- 看板式订单管理
- 拖拽式状态更新
- 快速查看订单详情

### 3. 订单管理
- 订单列表查看
- 创建/编辑订单
- 订单详情查看
- 状态筛选

### 4. 日历排期
- 月度日历视图
- 活动事件标记
- 快速查看活动详情

### 5. SKU管理
- SKU列表展示
- 库存实时监控
- 部件组成查看

### 6. 采购管理
- 采购单管理
- 部件库存监控
- 出入库流水

## API接口

系统提供RESTful API接口：

- `GET /api/orders/` - 获取订单列表
- `GET /api/skus/` - 获取SKU列表
- `GET /api/parts/` - 获取部件库存
- `GET /api/dashboard/stats/` - 获取统计数据

## 开发说明

### Mock数据
系统使用Mock数据进行演示，数据定义在 `apps/core/mock_data.py` 中。

### 添加新功能
1. 在 `apps/core/views.py` 中添加视图函数
2. 在 `apps/core/urls.py` 中配置路由
3. 在 `templates/` 中创建模板
4. 在 `static/` 中添加样式和脚本

### 数据库切换
修改 `config/settings.py` 中的 `DATABASES` 配置：

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'your_db_name',
        'USER': 'your_db_user',
        'PASSWORD': 'your_db_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

## 生产部署建议

1. **安全设置**
   - 修改 `SECRET_KEY`
   - 设置 `DEBUG = False`
   - 配置 `ALLOWED_HOSTS`

2. **数据库**
   - 使用PostgreSQL或MySQL
   - 配置数据库备份

3. **静态文件**
   - 使用CDN加速
   - 配置Nginx服务

4. **性能优化**
   - 启用缓存
   - 配置负载均衡
   - 使用异步任务队列

## 许可证

MIT License

## 联系方式

如有问题或建议，请联系开发团队。

## 更新日志

### v1.0.0 (2024-02-27)
- 初始版本发布
- 完整的订单管理功能
- 采购管理模块
- 日历排期功能
- 工作台看板
