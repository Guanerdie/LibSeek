[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot '.env'
$ExampleFile = Join-Path $ProjectRoot '.env.example'
$ComposeFile = Join-Path $ProjectRoot 'compose.yaml'

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath $ExampleFile -Destination $EnvFile
    Write-Host 'Created .env. Update the PostgreSQL password and local authentication values before starting.' -ForegroundColor Yellow
    exit 1
}

function Get-DotEnvValue([string]$Name) {
    $prefix = "$Name="
    $value = ''
    foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
        if ($line.StartsWith($prefix, [StringComparison]::Ordinal)) {
            $value = $line.Substring($prefix.Length).Trim()
        }
    }
    return $value
}

$dotenvInterpolationNames = @(
    Get-Content -LiteralPath $EnvFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2]
            if ($value -match '\$(?:\{)?[A-Za-z_]') {
                $name
            }
        }
    } | Sort-Object -Unique
)
if ($dotenvInterpolationNames.Count -gt 0) {
    throw "Refusing variable interpolation in .env. Store literal values for these keys: $($dotenvInterpolationNames -join ', ')"
}

$applicationEnvironmentNames = @(
    Get-Content -LiteralPath $ExampleFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^([A-Z][A-Z0-9_]*)=') {
            $Matches[1]
        }
    }
    [regex]::Matches(
        (Get-Content -Raw -LiteralPath $ComposeFile -Encoding UTF8),
        '\$\{([A-Z][A-Z0-9_]*)'
    ) | ForEach-Object { $_.Groups[1].Value }
)
$processOverrides = @(
    Get-ChildItem Env: | Where-Object {
        $applicationEnvironmentNames -contains $_.Name -or
        $_.Name.StartsWith('COMPOSE_', [StringComparison]::OrdinalIgnoreCase)
    } | Select-Object -ExpandProperty Name | Sort-Object -Unique
)
if ($processOverrides.Count -gt 0) {
    throw "Refusing ambient configuration overrides. Clear these process environment variables and keep startup configuration in .env: $($processOverrides -join ', ')"
}

$composeEnvironmentNames = @(
    Get-Content -LiteralPath $EnvFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^(COMPOSE_[A-Z0-9_]*)=') {
            $Matches[1]
        }
    } | Sort-Object -Unique
)
$composeFileOverrides = @(
    $composeEnvironmentNames | Where-Object {
        -not [string]::IsNullOrEmpty((Get-DotEnvValue $_))
    }
)
if ($composeFileOverrides.Count -gt 0) {
    throw "Refusing Compose control variables in .env: $($composeFileOverrides -join ', ')"
}

$postgresPassword = Get-DotEnvValue 'POSTGRES_PASSWORD'
$databaseUrl = Get-DotEnvValue 'DATABASE_URL'
if (
    -not $postgresPassword -or
    -not $databaseUrl -or
    $postgresPassword.Contains('change-me-before-production') -or
    $databaseUrl.Contains('change-me-before-production')
) {
    throw 'Refusing to start with the example PostgreSQL password. Update POSTGRES_PASSWORD and DATABASE_URL in .env.'
}

$authUsername = Get-DotEnvValue 'AUTH_LOCAL_USERNAME'
$authPassword = Get-DotEnvValue 'AUTH_LOCAL_PASSWORD'
$authSigningKey = Get-DotEnvValue 'AUTH_SESSION_SIGNING_KEY'
if (-not $authUsername -or -not $authPassword -or $authSigningKey.Length -lt 32) {
    throw 'Local start requires AUTH_LOCAL_USERNAME, AUTH_LOCAL_PASSWORD, and an AUTH_SESSION_SIGNING_KEY of at least 32 characters.'
}

docker compose --project-directory $ProjectRoot --env-file $EnvFile -f $ComposeFile config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose validation failed with exit code $LASTEXITCODE"
}
if ($ValidateOnly) {
    Write-Host 'Docker Compose configuration is valid. No containers were started.' -ForegroundColor Green
    exit 0
}

docker compose --project-directory $ProjectRoot --env-file $EnvFile -f $ComposeFile up -d --build --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose startup failed with exit code $LASTEXITCODE"
}
docker compose --project-directory $ProjectRoot --env-file $EnvFile -f $ComposeFile ps
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose status check failed with exit code $LASTEXITCODE"
}
Write-Host 'Frontend: http://127.0.0.1:8080  API: http://127.0.0.1:8000/api/docs' -ForegroundColor Green
