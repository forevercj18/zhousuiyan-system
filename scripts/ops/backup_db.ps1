param(
    [string]$DbPath = ".\db.sqlite3",
    [string]$BackupDir = ".\backups"
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $DbPath)) {
    throw "数据库文件不存在: $DbPath"
}

if (!(Test-Path $BackupDir)) {
    New-Item -Path $BackupDir -ItemType Directory | Out-Null
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$name = "db_backup_$ts.sqlite3"
$target = Join-Path $BackupDir $name

Copy-Item -Path $DbPath -Destination $target -Force
Write-Host "[OK] 已备份到: $target"
