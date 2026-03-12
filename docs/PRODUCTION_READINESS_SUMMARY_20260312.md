# 生产就绪总结（2026-03-12）

## 1. 当前结论

项目代码和核心业务链路已达到“可进入生产准备”的状态：

- 订单状态主链路已收口
- 转寄候选/转寄任务状态表达已收口
- 单套库存、装配、维修、处置、回件质检链路可跑通
- 工作台、报表、导出、仓储看板已联动
- 全量自动化测试已通过

但正式上线前，仍需完成部署层配置收口。

## 2. 验证结果

- `python manage.py check`：通过
- `python manage.py showmigrations`：通过
- `python manage.py test -v 1`：通过
- 当前全量测试：`196/196`

## 3. 本轮巡检已修复问题

1. 订单中心关键词检索补齐：
   - 微信号
   - 闲鱼订单号

2. 采购/部件导航激活规则收口：
   - 改为 `url_name` 精确匹配
   - 避免多菜单同时高亮

3. 工作台/API 仓库可用库存口径统一：
   - 改为按 `SKU.effective_stock` 聚合
   - 兼容单套库存与历史未初始化单套数据

4. 审计中间件异常处理收口：
   - 去掉 `print`
   - 改为标准日志输出

5. 生产配置骨架补齐：
   - `config.settings_common`
   - `config.settings_dev`
   - `config.settings_prod`
   - `.env.prod.example`
   - `docker-compose.prod.yml`
   - `scripts/start_prod.sh`
   - `deploy/nginx.prod.conf`

## 4. 生产阻断项

### 阻断 1：生产环境不得使用 SQLite

- 生产必须使用 `config.settings_prod`
- 生产必须设置：
  - `DB_ENGINE=postgres`

### 阻断 2：生产环境不得使用默认 SECRET_KEY

- 必须在环境变量中提供真实 `SECRET_KEY`

### 阻断 3：必须配置正式域名与 CSRF 白名单

- 必须设置：
  - `ALLOWED_HOSTS`
  - `CSRF_TRUSTED_ORIGINS`

### 阻断 4：必须完成静态文件收集

- 必须执行：
  - `python manage.py collectstatic --noinput`

## 5. 建议上线前动作

1. 准备 `.env.prod`
2. 准备 PostgreSQL
3. 执行：
   - `python manage.py check --deploy`
   - `python manage.py migrate`
   - `python manage.py collectstatic --noinput`
4. 走一次真实业务验收：
   - 仓库直发闭环
   - 转寄闭环
   - 来源单占用闭环
   - 装配 -> 维修 -> 处置 -> 回件质检闭环
5. 备份：
   - 数据库
   - `media/`
   - 当前代码版本

## 6. 推荐上线方式

优先推荐：

- `config.settings_prod`
- PostgreSQL
- Gunicorn
- Nginx
- 可选 Docker Compose：
  - `docker-compose.prod.yml`

## 7. 配套文档

- `docs/PRODUCTION_DEPLOYMENT_GUIDE_20260312.md`
- `docs/PRODUCTION_RELEASE_CHECKLIST_20260312.md`
- `docs/ORDER_STATUS_MATRIX_20260311.md`
- `docs/TRANSFER_STATUS_MATRIX_20260311.md`
- `docs/INVENTORY_UNIT_NODE_MATRIX_20260311.md`
- `docs/ORDER_TRANSFER_UNIT_LINKAGE_OVERVIEW_20260311.md`

