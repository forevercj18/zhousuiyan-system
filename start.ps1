Param(
    [switch]$NoRunServer,
    [switch]$Acceptance,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

# Development defaults for local run
if (-not $env:DJANGO_SETTINGS_MODULE) {
    $env:DJANGO_SETTINGS_MODULE = "config.settings_dev"
}
if (-not $env:DEBUG) {
    $env:DEBUG = "True"
}
if (-not $env:ALLOWED_HOSTS) {
    $env:ALLOWED_HOSTS = "127.0.0.1,localhost,.trycloudflare.com"
}
if (-not $env:CSRF_TRUSTED_ORIGINS) {
    $env:CSRF_TRUSTED_ORIGINS = "https://*.trycloudflare.com,http://*.trycloudflare.com"
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
