Param(
    [int]$Port = 8000,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

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

function Import-EnvFile {
    Param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )
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
        $value = $parts[1].Trim().Trim('"')
        Set-Item -Path "Env:$name" -Value $value
    }
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env.prod"

Write-Step "Check production env file"
if (!(Test-Path $envFile)) {
    throw ".env.prod not found. Please copy .env.prod.example to .env.prod and fill it first."
}
Import-EnvFile -Path $envFile
$env:DJANGO_SETTINGS_MODULE = "config.settings_prod"

Write-Step "Check virtual environment"
if (!(Test-Path $venvPython)) {
    Write-Step "Create .venv"
    python -m venv .venv
}
Write-Ok "Virtual environment ready"

Write-Step "Install dependencies"
Invoke-Checked -File $venvPython -Arguments @("-m", "pip", "install", "-r", "requirements.txt")
Write-Ok "Dependencies installed"

Write-Step "Run production deploy check"
Invoke-Checked -File $venvPython -Arguments @("manage.py", "check", "--deploy")
Write-Ok "Deploy check passed"

Write-Step "Apply migrations"
Invoke-Checked -File $venvPython -Arguments @("manage.py", "migrate", "--noinput")
Write-Ok "Migrations done"

Write-Step "Collect static files"
Invoke-Checked -File $venvPython -Arguments @("manage.py", "collectstatic", "--noinput")
Write-Ok "Static files collected"

if ($NoStart) {
    Write-Ok "Skip HTTP server startup because NoStart is set"
    exit 0
}

Write-Step "Start Waitress at http://0.0.0.0:$Port"
Invoke-Checked -File $venvPython -Arguments @("-m", "waitress", "--listen=0.0.0.0:$Port", "config.wsgi:application")
