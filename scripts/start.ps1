$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot '.env'
$ExampleFile = Join-Path $ProjectRoot '.env.example'

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath $ExampleFile -Destination $EnvFile
    Write-Host 'Created .env. Update the PostgreSQL password and local NextFind credentials before starting.' -ForegroundColor Yellow
    exit 1
}

docker compose --project-directory $ProjectRoot config --quiet
docker compose --project-directory $ProjectRoot up -d --build
docker compose --project-directory $ProjectRoot ps
Write-Host 'Frontend: http://127.0.0.1:8080  API: http://127.0.0.1:8000/api/docs' -ForegroundColor Green
