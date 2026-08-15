[CmdletBinding()]
param(
    [string]$DockerContext,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot '.env'
$ExampleFile = Join-Path $ProjectRoot '.env.example'
$ComposeFile = Join-Path $ProjectRoot 'compose.yaml'
$DockerClientEnvironmentNames = @(
    'DOCKER_HOST',
    'DOCKER_CONTEXT',
    'DOCKER_TLS_VERIFY',
    'DOCKER_CERT_PATH',
    'DOCKER_CONFIG'
)

if (
    [string]::IsNullOrWhiteSpace($DockerContext) -or
    $DockerContext.Length -gt 128 -or
    $DockerContext -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$'
) {
    throw 'Docker startup requires an explicit -DockerContext with a safe context name.'
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
        $DockerClientEnvironmentNames -contains $_.Name -or
        $_.Name.StartsWith('COMPOSE_', [StringComparison]::OrdinalIgnoreCase)
    } | Select-Object -ExpandProperty Name | Sort-Object -Unique
)
if ($processOverrides.Count -gt 0) {
    throw "Refusing ambient configuration overrides. Clear these process environment variables and keep startup configuration in .env: $($processOverrides -join ', ')"
}

if (-not (Test-Path -LiteralPath $EnvFile)) {
    $randomBytes = New-Object byte[] 32
    $randomGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $randomGenerator.GetBytes($randomBytes)
    }
    finally {
        $randomGenerator.Dispose()
    }
    $postgresPassword = ([BitConverter]::ToString($randomBytes)).Replace('-', '').ToLowerInvariant()
    $contents = (Get-Content -Raw -LiteralPath $ExampleFile -Encoding UTF8).Replace(
        'change-me-before-production',
        $postgresPassword
    )
    [IO.File]::WriteAllText($EnvFile, $contents, [Text.UTF8Encoding]::new($false))
    Write-Host 'Created local .env with a random PostgreSQL password.' -ForegroundColor Green
}

$unsupportedDotEnvLineNumbers = @(
    $lineNumber = 0
    Get-Content -LiteralPath $EnvFile -Encoding UTF8 | ForEach-Object {
        $lineNumber += 1
        if (
            -not [string]::IsNullOrWhiteSpace($_) -and
            -not $_.TrimStart().StartsWith('#', [StringComparison]::Ordinal) -and
            $_ -cnotmatch '^[A-Z][A-Z0-9_]*=.*$'
        ) {
            $lineNumber
        }
    }
)
if ($unsupportedDotEnvLineNumbers.Count -gt 0) {
    throw "Refusing unsupported .env syntax on line(s): $($unsupportedDotEnvLineNumbers -join ', '). Only blank lines, comments, and strict uppercase KEY=VALUE entries are allowed."
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

$composeArguments = @(
    '--context', $DockerContext,
    'compose',
    '--project-directory', $ProjectRoot,
    '--env-file', $EnvFile,
    '-f', $ComposeFile
)
if ((Get-DotEnvValue 'ENABLE_DOWNLOAD_EXECUTOR') -ceq 'true') {
    $composeArguments += @('--profile', 'download-execution')
}
if ((Get-DotEnvValue 'ENABLE_DOWNLOAD_MONITOR') -ceq 'true') {
    $composeArguments += @('--profile', 'download-monitor')
}

& docker @composeArguments config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose validation failed with exit code $LASTEXITCODE"
}
if ($ValidateOnly) {
    Write-Host 'Docker Compose configuration is valid. No containers were started.' -ForegroundColor Green
    exit 0
}

& docker @composeArguments up -d --build --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose startup failed with exit code $LASTEXITCODE"
}
& docker @composeArguments ps
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose status check failed with exit code $LASTEXITCODE"
}
Write-Host 'Open http://127.0.0.1:9527 to create the local administrator and configure connections.' -ForegroundColor Green
