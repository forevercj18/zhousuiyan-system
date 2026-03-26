Param(
    [switch]$NoRunServer,
    [switch]$Acceptance,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

function Import-EnvFile {
    Param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string[]]$OnlyNames = @(),
        [switch]$SkipExisting
    )

    if (!(Test-Path $Path)) {
        return
    }

    $onlyMap = @{}
    foreach ($name in $OnlyNames) {
        $onlyMap[$name] = $true
    }

    Get-Content $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) {
            return
        }
        $parts = $line.Split('=', 2)
        if ($parts.Count -ne 2) {
            return
        }
        $name = $parts[0].Trim()
        if ($onlyMap.Count -gt 0 -and -not $onlyMap.ContainsKey($name)) {
            return
        }
        if ($SkipExisting -and (Test-Path "Env:$name") -and -not [string]::IsNullOrWhiteSpace((Get-Item "Env:$name").Value)) {
            return
        }
        $value = $parts[1].Trim().Trim('"')
        Set-Item -Path "Env:$name" -Value $value
    }
}

# Development defaults for local run
if (-not $env:DJANGO_SETTINGS_MODULE) {
    $env:DJANGO_SETTINGS_MODULE = "config.settings_dev"
}
if (-not $env:DEBUG) {
    $env:DEBUG = "True"
}
if (-not $env:ALLOWED_HOSTS) {
    $env:ALLOWED_HOSTS = "127.0.0.1,localhost,.trycloudflare.com,.yanli.net.cn,erp.yanli.net.cn"
}
if (-not $env:CSRF_TRUSTED_ORIGINS) {
    $env:CSRF_TRUSTED_ORIGINS = "https://*.trycloudflare.com,http://*.trycloudflare.com,https://erp.yanli.net.cn,http://erp.yanli.net.cn"
}

function Write-Step($msg) {
    Write-Host "[STEP] $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "[OK] $msg" -ForegroundColor Green
}

function Invoke-Checked {
    Param(
        [Parameter(Mandatory = $true)]
        [string]$File,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $File $($Arguments -join ' ') (exit=$LASTEXITCODE)"
    }
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$devEnvFile = Join-Path $projectRoot ".env"
$prodEnvFile = Join-Path $projectRoot ".env.prod"

Import-EnvFile -Path $devEnvFile -SkipExisting
Import-EnvFile -Path $prodEnvFile -OnlyNames @(
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "R2_ENDPOINT",
    "R2_PUBLIC_DOMAIN",
    "R2_REGION",
    "R2_UPLOAD_PREFIX_SKU",
    "R2_UPLOAD_EXPIRE",
    "QINIU_ACCESS_KEY",
    "QINIU_SECRET_KEY",
    "QINIU_BUCKET",
    "QINIU_DOMAIN",
    "QINIU_UPLOAD_URL",
    "QINIU_UPLOAD_PREFIX_SKU",
    "QINIU_UPLOAD_TOKEN_EXPIRE",
    "MP_PUBLIC_BASE_URL"
) -SkipExisting

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

Write-Step "Check virtual environment"
if (!(Test-Path $venvPython)) {
    Write-Step "Create .venv"
    python -m venv .venv
}
Write-Ok "Virtual environment ready"

Write-Step "Install dependencies"
Invoke-Checked -File $venvPython -Arguments @("-m", "pip", "install", "-r", "requirements.txt")
Write-Ok "Dependencies installed"

Write-Step "Run migrations"
Invoke-Checked -File $venvPython -Arguments @("manage.py", "migrate")
Write-Ok "Migrations done"

Write-Step "Init demo data"
Invoke-Checked -File $venvPython -Arguments @("scripts/init_data.py")
Write-Ok "Init data done"

if ($Acceptance) {
    Write-Step "Generate acceptance report"
    Invoke-Checked -File $venvPython -Arguments @("scripts/generate_acceptance_report.py")
    Write-Ok "Acceptance report generated in docs/"
}

if ($NoRunServer) {
    Write-Ok "Skip runserver because NoRunServer is set"
    exit 0
}

Write-Step "Start dev server at http://127.0.0.1:$Port"
Invoke-Checked -File $venvPython -Arguments @("manage.py", "runserver", "0.0.0.0:$Port")
