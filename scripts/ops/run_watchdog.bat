@echo off
setlocal

set ROOT=%~dp0..\..
cd /d %ROOT%

if not exist .\.venv\Scripts\python.exe (
  echo [ERROR] 未找到虚拟环境 Python: .\.venv\Scripts\python.exe
  exit /b 1
)

echo [STEP] 运行运维告警聚合
.\.venv\Scripts\python.exe manage.py ops_watchdog --json --notify --save-audit
if errorlevel 1 (
  echo [ERROR] ops_watchdog 执行失败
  exit /b 1
)

echo [STEP] 运行审批SLA催办
.\.venv\Scripts\python.exe manage.py approval_sla_remind --hours 24 --limit 200 --notify
if errorlevel 1 (
  echo [ERROR] approval_sla_remind 执行失败
  exit /b 1
)

echo [OK] watchdog 执行完成
endlocal
