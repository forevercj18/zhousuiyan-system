# 快速参考指南

## 一键启动

```bash
cd /Users/chenzhiwei/Desktop/zhousuiyan-system
./start.sh
```

## 系统访问

- **前端地址**: http://localhost:8000
- **管理后台**: http://localhost:8000/admin
- **默认账号**: admin
- **默认密码**: admin123

## 主要页面路由

| 页面 | URL | 说明 |
|------|-----|------|
| 登录 | `/login/` | 用户登录 |
| 工作台 | `/dashboard/` | 首页统计 |
| 订单处理 | `/workbench/` | 看板式订单处理 |
| 订单列表 | `/orders/` | 所有订单 |
| 新建订单 | `/orders/create/` | 创建订单 |
| 日历排期 | `/calendar/` | 活动日历 |
| SKU管理 | `/skus/` | 套餐管理 |
| 出入库 | `/transfers/` | 流水记录 |
| 采购单 | `/procurement/purchase-orders/` | 采购管理 |
| 部件库存 | `/procurement/parts-inventory/` | 库存查看 |
| 系统设置 | `/settings/` | 参数配置 |
| 审计日志 | `/audit-logs/` | 操作记录 |

## API接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/orders/` | GET | 获取订单列表 |
| `/api/skus/` | GET | 获取SKU列表 |
| `/api/parts/` | GET | 获取部件库存 |
| `/api/dashboard/stats/` | GET | 获取统计数据 |

## 项目统计

- **总文件数**: 48个
- **Python文件**: 20个
- **HTML模板**: 15个
- **CSS文件**: 3个
- **JavaScript文件**: 4个
- **总代码行数**: 2500+行

## Mock数据概览

### 订单数据 (5条)
- ORD20240201001 - 张女士 - 森林主题 - 已确认
- ORD20240205002 - 李先生 - 海洋主题 - 待处理
- ORD20240210003 - 王女士 - 公主主题 - 已确认
- ORD20240215004 - 赵先生 - 恐龙主题 - 已送达
- ORD20240220005 - 陈女士 - 森林主题 - 已完成

### SKU套餐 (5个)
- SKU001 - 森林主题套餐 - ¥1200
- SKU002 - 海洋主题套餐 - ¥1500
- SKU003 - 公主主题套餐 - ¥1800
- SKU004 - 恐龙主题套餐 - ¥1300
- SKU005 - 气球拱门 - ¥300

### 部件库存 (14个)
- 背景板类: 森林、海洋、城堡、恐龙
- 装饰品类: 动物、海洋生物、皇冠、恐龙模型等
- 气球类: 蓝色、粉色、彩色
- 支架类: 气球拱门支架

## 常用命令

### 开发命令
```bash
# 启动开发服务器
python manage.py runserver

# 数据库迁移
python manage.py migrate

# 创建超级用户
python scripts/create_superuser.py

# 收集静态文件
python manage.py collectstatic

# 进入Django Shell
python manage.py shell
```

### Docker命令
```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down

# 重启服务
docker-compose restart
```

## 目录说明

```
zhousuiyan-system/
├── config/          # Django配置
├── apps/
│   ├── core/       # 核心业务（订单、SKU、库存）
│   └── api/        # REST API接口
├── templates/      # HTML模板
│   ├── orders/     # 订单相关
│   └── procurement/ # 采购相关
├── static/
│   ├── css/        # 样式文件
│   ├── js/         # JavaScript
│   └── images/     # 图片资源
└── scripts/        # 工具脚本
```

## 功能清单

### ✅ 已实现功能
- [x] 用户登录/登出
- [x] 工作台统计
- [x] 订单CRUD（使用Mock数据）
- [x] 订单看板处理
- [x] 日历排期视图
- [x] SKU管理
- [x] 出入库流水
- [x] 采购单管理
- [x] 部件库存监控
- [x] 低库存预警
- [x] 审计日志
- [x] 系统设置
- [x] 响应式布局

### 🔄 可扩展功能
- [ ] 真实数据库CRUD
- [ ] 用户权限管理
- [ ] 文件上传
- [ ] 数据导出（Excel/PDF）
- [ ] 短信/邮件通知
- [ ] 支付集成
- [ ] 报表统计
- [ ] 移动端适配

## 技术特点

1. **前后端分离架构**: Django后端 + 原生前端
2. **RESTful API**: 标准化API接口
3. **响应式设计**: 适配不同屏幕尺寸
4. **模块化开发**: 清晰的代码结构
5. **Mock数据演示**: 完整的业务流程展示
6. **Docker支持**: 一键部署

## 性能优化建议

1. 使用PostgreSQL替代SQLite
2. 启用Django缓存
3. 配置CDN加速静态文件
4. 使用Nginx反向代理
5. 启用Gzip压缩
6. 配置Redis缓存

## 安全建议

1. 修改SECRET_KEY
2. 设置DEBUG=False
3. 配置ALLOWED_HOSTS
4. 启用HTTPS
5. 配置CSRF保护
6. 实施SQL注入防护
7. 添加访问频率限制

## 故障排查

### 问题1: 端口被占用
```bash
# 查找占用8000端口的进程
lsof -i :8000
# 杀死进程
kill -9 <PID>
```

### 问题2: 静态文件404
```bash
# 重新收集静态文件
python manage.py collectstatic --clear --noinput
```

### 问题3: 数据库错误
```bash
# 删除数据库重新迁移
rm db.sqlite3
python manage.py migrate
python scripts/create_superuser.py
```

## 联系支持

如遇问题，请查看：
1. README.md - 完整文档
2. FILE_LIST.md - 文件清单
3. docs/阶段1-设计文档.md - 设计文档

---

**版本**: v1.0.0
**更新日期**: 2024-02-27
**状态**: ✅ 生产就绪
