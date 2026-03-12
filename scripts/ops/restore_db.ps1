param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [string]$DbPath = ".\db.sqlite3"
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $BackupFile)) {
    throw "备份文件不存在: $BackupFile"
}

if (Test-Path $DbPath) {
    $bak = "$DbPath.pre_restore_" + (Get-Date -Format "yyyyMMdd_HHmmss")
    Copy-Item -Path $DbPath -Destination $bak -Force
    Write-Host "[INFO] 现有数据库已备份: $bak"
}

Copy-Item -Path $BackupFile -Destination $DbPath -Force
Write-Host "[OK] 已恢复数据库: $DbPath"
