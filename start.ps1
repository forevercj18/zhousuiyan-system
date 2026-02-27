Param(
    [switch]$NoRunServer,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

function Write-Step($msg) {
    Write-Host "[STEP] $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "[OK] $msg" -ForegroundColor Green
}

function Invoke-Checked([string]$file, [string[]]$args) {
    & $file @args
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $file $($args -join ' ') (exit=$LASTEXITCODE)"
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
Invoke-Checked $venvPython @("-m", "pip", "install", "-r", "requirements.txt")
Write-Ok "Dependencies installed"

Write-Step "Run migrations"
Invoke-Checked $venvPython @("manage.py", "migrate")
Write-Ok "Migrations done"

Write-Step "Init demo data"
Invoke-Checked $venvPython @("scripts/init_data.py")
Write-Ok "Init data done"

if ($NoRunServer) {
    Write-Ok "Skip runserver because NoRunServer is set"
    exit 0
}

Write-Step "Start dev server at http://127.0.0.1:$Port"
Invoke-Checked $venvPython @("manage.py", "runserver", "0.0.0.0:$Port")
