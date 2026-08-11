[CmdletBinding()]
param(
    [string]$DockerContext,
    [switch]$Discovery,
    [switch]$AvistaZ,
    [switch]$Qb,
    [string[]]$Profile = @(),
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot '.env'
$ExampleFile = Join-Path $ProjectRoot '.env.example'
$ComposeFile = Join-Path $ProjectRoot 'compose.yaml'
$DiscoveryComposeFile = Join-Path $ProjectRoot 'deploy/compose.secrets.discovery.yaml.example'
$AvistaZComposeFile = Join-Path $ProjectRoot 'deploy/compose.secrets.avistaz.yaml.example'
$QbComposeFile = Join-Path $ProjectRoot 'deploy/compose.secrets.qb.yaml.example'
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
    throw 'Docker Secret startup requires an explicit -DockerContext with a safe context name.'
}

if (-not $Discovery) {
    throw 'Docker Secret startup requires the explicit -Discovery layer. Add -AvistaZ, -Qb, and -Profile only when those stages are authorized.'
}

$profileOrder = @('automation-preflight', 'download-execution', 'download-monitor')
$requestedProfiles = @(
    $Profile | ForEach-Object { $_ -split ',' } | Where-Object { $_ }
)
$unknownProfiles = @($requestedProfiles | Where-Object { $profileOrder -notcontains $_ })
if ($unknownProfiles.Count -gt 0) {
    throw 'Unsupported profile. Allowed profiles: automation-preflight, download-execution, download-monitor.'
}
$selectedProfiles = @($profileOrder | Where-Object { $requestedProfiles -contains $_ })
if (
    ($selectedProfiles -contains 'automation-preflight' -or
     $selectedProfiles -contains 'download-monitor') -and
    -not $Qb
) {
    throw 'The automation-preflight and download-monitor profiles require the explicit -Qb Secret layer.'
}
if (
    $selectedProfiles -contains 'download-execution' -and
    (-not $AvistaZ -or -not $Qb)
) {
    throw 'The download-execution profile requires both the explicit -AvistaZ and -Qb Secret layers.'
}

$composeFiles = @($ComposeFile, $DiscoveryComposeFile)
if ($AvistaZ) {
    $composeFiles += $AvistaZComposeFile
}
if ($Qb) {
    $composeFiles += $QbComposeFile
}
foreach ($path in @($ExampleFile) + $composeFiles) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required repository file is missing: $path"
    }
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

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw 'Missing .env. Copy .env.example to .env and configure it before Docker Secret startup.'
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

$plaintextSecretEnvironmentNames = @(
    'AUTH_LOCAL_USERNAME',
    'AUTH_LOCAL_PASSWORD',
    'AUTH_SESSION_SIGNING_KEY',
    'NEXTFIND_USERNAME',
    'NEXTFIND_PASSWORD',
    'TMDB_ACCESS_TOKEN',
    'AVISTAZ_USERNAME',
    'AVISTAZ_PASSWORD',
    'AVISTAZ_PID',
    'QB_BASE_URL',
    'QB_USERNAME',
    'QB_PASSWORD'
)
$nonEmptyPlaintextSecretNames = @(
    Get-Content -LiteralPath $EnvFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
            $name = $Matches[1]
            if (
                $plaintextSecretEnvironmentNames -contains $name -and
                -not [string]::IsNullOrWhiteSpace($Matches[2])
            ) {
                $name
            }
        }
    } | Sort-Object -Unique
)
if ($nonEmptyPlaintextSecretNames.Count -gt 0) {
    throw "Refusing plaintext credentials in .env while using Docker Secrets. Clear these keys: $($nonEmptyPlaintextSecretNames -join ', ')"
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

$secretNames = @(
    'auth_local_username.txt',
    'auth_local_password.txt',
    'auth_session_signing_key.txt',
    'nextfind_username.txt',
    'nextfind_password.txt',
    'tmdb_access_token.txt'
)
if ($AvistaZ) {
    $secretNames += @('avistaz_username.txt', 'avistaz_password.txt', 'avistaz_pid.txt')
}
if ($Qb) {
    $secretNames += @('qb_base_url.txt', 'qb_username.txt', 'qb_password.txt')
}
$missingSecretNames = @(
    $secretNames | Where-Object {
        -not (Test-Path -LiteralPath (Join-Path $ProjectRoot "secrets/$_") -PathType Leaf)
    }
)
if ($missingSecretNames.Count -gt 0) {
    throw "Missing selected Docker Secret files: $($missingSecretNames -join ', ')"
}

$composeArguments = @(
    '--context', $DockerContext,
    'compose',
    '--project-directory', $ProjectRoot,
    '--env-file', $EnvFile
)
foreach ($path in $composeFiles) {
    $composeArguments += @('-f', $path)
}
foreach ($name in $selectedProfiles) {
    $composeArguments += @('--profile', $name)
}

& docker @composeArguments config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose validation failed with exit code $LASTEXITCODE"
}
if ($ValidateOnly) {
    Write-Host 'Docker Compose Secret configuration is valid. No containers were started.' -ForegroundColor Green
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
Write-Host 'Frontend: http://127.0.0.1:8080  API: http://127.0.0.1:8000/api/docs' -ForegroundColor Green
