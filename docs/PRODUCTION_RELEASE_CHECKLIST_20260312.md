# 生产发布检查清单（2026-03-12）

## 阻断项

- [ ] 使用 `config.settings_prod`
- [ ] 未使用 SQLite
- [ ] `SECRET_KEY` 已替换
- [ ] `ALLOWED_HOSTS` 已配置
- [ ] `CSRF_TRUSTED_ORIGINS` 已配置
- [ ] `python manage.py check --deploy` 通过
- [ ] `python manage.py test -v 1` 通过
- [ ] `python manage.py migrate` 通过
- [ ] `python manage.py collectstatic --noinput` 通过

## 文件系统

- [ ] `media/` 目录可写
- [ ] `logs/` 目录可写
- [ ] 静态资源可访问
- [ ] 若使用 Docker，`.env.prod` 已创建
- [ ] 若使用 Docker，`docker-compose.prod.yml` 已验证可启动

## 数据

- [ ] 数据库已备份
- [ ] 媒体文件已备份
- [ ] 关键管理员账号已验证

## 业务验收

- [ ] 仓库直发闭环
- [ ] 转寄闭环
- [ ] 来源单被占用闭环
- [ ] 装配 -> 维修 -> 处置 -> 回件质检闭环
- [ ] 工作台卡片跳转正常
- [ ] 报表导出正常

## 观察期

- [ ] 应用日志正常写入
- [ ] 无 500/403 异常高频出现
- [ ] 审计日志持续产生
