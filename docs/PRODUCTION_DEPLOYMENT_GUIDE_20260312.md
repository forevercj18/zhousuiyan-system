# 生产部署指南（2026-03-12）

## 1. 设置模块

- 本地开发：`config.settings` 或 `config.settings_dev`
- 生产环境：`config.settings_prod`

示例：

```powershell
$env:DJANGO_SETTINGS_MODULE='config.settings_prod'
```

## 2. 环境变量

复制根目录 `.env.prod.example`，按实际环境填写：

- `SECRET_KEY`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `DB_*`

说明：

- `*.trycloudflare.com` 只适合临时开发外网访问，不建议作为正式生产域名。
- 正式生产应填写固定域名，例如 `example.com,www.example.com`。
- `CSRF_TRUSTED_ORIGINS` 必须写完整协议头，例如 `https://example.com,https://www.example.com`。
- 若使用 PostgreSQL，Python 环境必须已安装驱动：`psycopg2-binary`（已写入 `requirements.txt`）。

生产环境禁止：

- 使用默认 `SECRET_KEY`
- 使用 `SQLite`

## 3. 推荐部署步骤

```powershell
.venv\Scripts\python manage.py check --deploy
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py collectstatic --noinput
```

然后启动 WSGI/ASGI：

- WSGI：`config.wsgi`
- ASGI：`config.asgi`

## 3.1 容器化部署样板

已提供：

- `docker-compose.prod.yml`
- `scripts/start_prod.sh`
- `deploy/nginx.prod.conf`

典型步骤：

```bash
cp .env.prod.example .env.prod
docker compose -f docker-compose.prod.yml up -d --build
```

启动链路：

1. `web` 容器执行：
   - `manage.py check --deploy`
   - `migrate`
   - `collectstatic`
   - `gunicorn`
2. `nginx`：
   - 代理 Django
   - 提供 `/static/`
   - 提供 `/media/`
3. `db`：
   - PostgreSQL 16

## 4. 上线前必须确认

1. `DJANGO_SETTINGS_MODULE=config.settings_prod`
2. `DB_ENGINE=postgres`
3. `ALLOWED_HOSTS` 已配置正式域名
4. `CSRF_TRUSTED_ORIGINS` 已配置正式 HTTPS 域名
5. 已执行 `collectstatic`
6. `logs/` 可写
7. `media/` 可写
8. 若使用 Docker，`.env.prod` 已准备完成

## 5. 回滚建议

上线前保留：

- 数据库备份
- `media/` 备份
- 上一个版本代码包

回滚顺序：

1. 停止当前服务
2. 恢复旧代码
3. 恢复数据库
4. 恢复媒体文件
5. 重新启动服务
