[CmdletBinding()]
param()

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

Push-Location (Join-Path $projectRoot 'backend')
try {
    & uv run pytest -q --basetemp=.pytest-tmp-script
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & uv run ruff check app tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & uv run mypy app
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}

Push-Location (Join-Path $projectRoot 'frontend')
try {
    & npm test
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & npm run lint
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
