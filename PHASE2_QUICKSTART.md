# 第二阶段快速启动指南

## 🚀 快速开始

### 1. 重新初始化数据库

```bash
cd /Users/chenzhiwei/Desktop/zhousuiyan-system

# 删除旧数据库
rm -f db.sqlite3

# 执行迁移
python3 manage.py migrate

# 初始化数据（创建用户、SKU、部件等）
python3 scripts/init_data.py
```

### 2. 启动服务器

```bash
python3 manage.py runserver 8000
```

### 3. 访问系统

浏览器访问：**http://localhost:8000**

## 🔑 测试账号

| 用户名 | 密码 | 角色 | 权限 |
|--------|------|------|------|
| admin | admin123 | 超级管理员 | 所有权限 |
| manager_zhang | zhang123 | 业务经理 | 订单、财务、排期 |
| warehouse_li | li123 | 仓库主管 | 工作台、SKU、采购、部件 |
| staff_wang | wang123 | 仓库操作员 | 工作台、部件出入库 |
| cs_liu | liu123 | 客服 | 订单查看、创建 |

## ✅ 已实现的功能

### 核心功能
- ✅ 数据库模型（11个模型）
- ✅ 订单业务逻辑（创建、确认、送达、归还、完成）
- ✅ 库存校验算法
- ✅ 采购入库自动更新部件库存
- ✅ 部件出入库管理
- ✅ 权限控制系统（5种角色）
- ✅ 操作日志自动记录
- ✅ REST API接口（核心部分）
- ✅ 前后端联调完成（所有页面已连接真实数据）

### 可以测试的功能
1. **用户登录** - 使用不同角色登录，查看菜单权限差异
2. **工作台** - 查看真实统计数据、最近订单、库存预警
3. **订单管理** - 创建订单（含库存校验）、编辑、查看详情
4. **部件管理** - 部件入库/出库功能已实现
5. **采购管理** - 采购单入库会自动更新部件库存
6. **操作日志** - 所有操作会自动记录日志
7. **权限控制** - 不同角色看到不同的菜单和数据

## 📊 数据库状态

初始化后的数据：
- 5个用户（5种角色）
- 5个SKU（租赁套装）
- 14个部件
- 4个系统设置

## 🔧 开发命令

```bash
# 查看数据库表
python3 manage.py dbshell
.tables

# 创建新的迁移
python3 manage.py makemigrations

# 执行迁移
python3 manage.py migrate

# 创建超级用户
python3 manage.py createsuperuser

# 进入Django Shell
python3 manage.py shell
```

## 📝 下一步工作

第二阶段已全部完成！✅

查看详细报告：
- `docs/阶段2-开发进度报告.md` - 第二阶段整体进度
- `docs/阶段2-前后端联调完成报告.md` - 前后端联调详细说明

**已完成的5个任务：**
1. ✅ 数据库模型设计
2. ✅ 核心业务逻辑
3. ✅ 权限控制系统
4. ✅ REST API接口
5. ✅ 前后端联调

**可选的后续优化：**
- 添加分页功能
- 完善剩余API接口
- 优化前端交互体验
- 添加数据导出功能
- 准备生产环境部署

## 🎯 核心文件位置

```
apps/core/
├── models.py              # 数据库模型
├── utils.py               # 工具函数（库存校验等）
├── permissions.py         # 权限控制
├── middleware.py          # 操作日志中间件
└── services/
    ├── order_service.py       # 订单业务逻辑
    └── procurement_service.py # 采购业务逻辑

apps/api/
├── serializers.py         # API序列化器
└── views.py               # API视图

scripts/
└── init_data.py           # 初始化数据脚本
```

## 💡 提示

- 第一次启动前务必执行初始化脚本
- 不同角色登录会看到不同的菜单
- 操作日志会自动记录（查看"操作日志"页面）
- 部件入库/出库会实时更新库存

---

**文档更新时间**：2026-02-27
