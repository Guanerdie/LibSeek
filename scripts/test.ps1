$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Push-Location (Join-Path $ProjectRoot 'backend')
try {
    uv sync --extra dev
    uv run pytest
    uv run ruff check .
    uv run mypy app
}
finally { Pop-Location }

Push-Location (Join-Path $ProjectRoot 'frontend')
try {
    npm.cmd ci
    npm.cmd test
    npm.cmd run build
    npm.cmd run lint
}
finally { Pop-Location }

docker compose --project-directory $ProjectRoot --env-file (Join-Path $ProjectRoot '.env.example') config --quiet

