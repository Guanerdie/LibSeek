$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

Push-Location (Join-Path $ProjectRoot 'backend')
try {
    Invoke-NativeCommand { uv sync --extra dev } 'Backend dependency sync'
    Invoke-NativeCommand { uv run pytest } 'Backend tests'
    Invoke-NativeCommand { uv run ruff check . } 'Backend lint'
    Invoke-NativeCommand { uv run mypy app } 'Backend type check'
    Invoke-NativeCommand { uv run alembic upgrade head --sql | Out-Null } 'Alembic offline migration'
}
finally { Pop-Location }

Push-Location (Join-Path $ProjectRoot 'frontend')
try {
    Invoke-NativeCommand { npm.cmd ci } 'Frontend dependency install'
    Invoke-NativeCommand { npm.cmd test } 'Frontend tests'
    Invoke-NativeCommand { npm.cmd run build } 'Frontend build'
    Invoke-NativeCommand { npm.cmd run lint } 'Frontend lint'
}
finally { Pop-Location }

& (Join-Path $PSScriptRoot 'verify-compose.ps1')
& (Join-Path $PSScriptRoot 'verify-startup-gates.ps1')
