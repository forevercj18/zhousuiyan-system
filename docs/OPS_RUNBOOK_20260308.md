# 运维上线与回滚手册（2026-03-08）

## 1. 日常巡检（建议每30分钟）
1. 执行：
```bat
scripts\ops\run_watchdog.bat
```
2. 命令内容：
- `ops_watchdog --json --notify --save-audit`
- `approval_sla_remind --hours 24 --limit 200 --notify`

## 2. 数据库备份（上线前/日常）
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ops\backup_db.ps1
```

## 3. 数据库恢复（回滚）
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ops\restore_db.ps1 -BackupFile .\backups\db_backup_YYYYMMDD_HHMMSS.sqlite3
```

## 4. 通知配置（系统设置 -> 系统与界面）
- `alert_notify_enabled`: `0/1`
- `alert_notify_min_severity`: `info/warning/danger`
- `alert_notify_webhook_url`: 外部告警接收地址

## 5. 核心健康检查
```powershell
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\python manage.py test -v 1
.\.venv\Scripts\python manage.py check_consistency --json
```

## 6. 故障分流建议
- 业务异常（订单/转寄）：先看“转寄中心 + 风险事件”。
- 财务异常：先看“财务对账中心”并一键生成风险事件。
- 系统一致性异常：先 `repair_consistency --json` 预览，再决定是否 `--apply`。

## 7. 注意事项
- `repair_consistency` 默认是 `dry-run`，不会写库。
- `--fix-duplicate-locked` 属于可选修复项，建议先备份数据库再执行。
