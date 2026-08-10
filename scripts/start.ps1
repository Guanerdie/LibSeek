$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot '.env'
$ExampleFile = Join-Path $ProjectRoot '.env.example'

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath $ExampleFile -Destination $EnvFile
    Write-Host '已创建 .env。请先修改 PostgreSQL 密码；如需发现任务，再在本机填写 NextFind 运行时凭据。' -ForegroundColor Yellow
    exit 1
}

docker compose --project-directory $ProjectRoot config --quiet
docker compose --project-directory $ProjectRoot up -d --build
docker compose --project-directory $ProjectRoot ps
Write-Host '前端：http://127.0.0.1:8080  API：http://127.0.0.1:8000/api/docs' -ForegroundColor Green

