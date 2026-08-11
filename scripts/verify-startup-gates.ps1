$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PowerShellStart = Join-Path $PSScriptRoot 'start.ps1'
$ShellStart = Join-Path $PSScriptRoot 'start.sh'

$ComposeEnvironment = @(
    [regex]::Matches(
        (Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot 'compose.yaml') -Encoding UTF8),
        '\$\{([A-Z][A-Z0-9_]*)'
    ) | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
)
$ApplicationEnvironment = @(
    Get-Content -LiteralPath (Join-Path $ProjectRoot '.env.example') -Encoding UTF8 |
        ForEach-Object {
            if ($_ -match '^([A-Z][A-Z0-9_]*)=') {
                $Matches[1]
            }
        }
)
$DangerousFlags = @($ApplicationEnvironment | Where-Object { $_.StartsWith('ENABLE_') })
$BlockedProcessEnvironment = @(
    'COMPOSE_PROFILES',
    'COMPOSE_FILE',
    'COMPOSE_ENV_FILES',
    'COMPOSE_NONSTANDARD_GATE_SENTINEL'
) + $ApplicationEnvironment + $ComposeEnvironment
$BlockedProcessEnvironment = @($BlockedProcessEnvironment | Sort-Object -Unique)
$PowerShellBlockedProcessEnvironment = @(
    $BlockedProcessEnvironment + 'compose_nonstandard_mixed_case_sentinel' |
        Sort-Object -Unique
)

function Assert-True {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Condition,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Set-SafeDotEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string[]]$Prefix = @(),
        [string[]]$Suffix = @()
    )

    $safeLines = @(
        'POSTGRES_PASSWORD=NON_SECRET_SAFE_DB_VALUE',
        'DATABASE_URL=postgresql+psycopg://unin:NON_SECRET_SAFE_DB_VALUE@postgres:5432/unin',
        'AUTH_LOCAL_USERNAME=non-secret-local-user',
        'AUTH_LOCAL_PASSWORD=NON_SECRET_LOCAL_PASSWORD_SENTINEL',
        'AUTH_SESSION_SIGNING_KEY=NON_SECRET_SIGNING_KEY_SENTINEL_0000000000000000',
        'COMPOSE_PROFILES='
    )
    foreach ($flag in $DangerousFlags) {
        $safeLines += "${flag}=false"
    }
    [IO.File]::WriteAllLines(
        $Path,
        [string[]]($Prefix + $safeLines + $Suffix),
        [Text.UTF8Encoding]::new($false)
    )
}

function Read-DockerCalls {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }
    return @(
        Get-Content -LiteralPath $Path -Encoding UTF8 |
            Where-Object { $_.Trim() } |
            ForEach-Object { ,($_ | ConvertFrom-Json) }
    )
}

function Assert-ValidateOnlyDockerCall {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Calls,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedRoot,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedEnvFile,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedComposeFile,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    Assert-True ($Calls.Count -eq 1) "$Label must invoke docker exactly once in validate-only mode."
    $expected = @(
        'compose',
        '--project-directory', $ExpectedRoot,
        '--env-file', $ExpectedEnvFile,
        '-f', $ExpectedComposeFile,
        'config', '--quiet'
    )
    Assert-True (
        (($Calls[0] | ConvertTo-Json -Compress) -eq ($expected | ConvertTo-Json -Compress))
    ) "$Label did not pin compose config to the repository .env and compose.yaml."
}

function Assert-StaticComposeContract {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $content = Get-Content -Raw -LiteralPath $Path -Encoding UTF8
    Assert-True ($content.Contains('--env-file')) "$Label does not pass --env-file."
    Assert-True ($content -match '(?m)(?:^|\s)-f(?:\s|$)') "$Label does not pass -f."
    foreach ($command in @('config', 'up', 'ps')) {
        Assert-True ($content -match "(?m)\b$command\b") "$Label does not contain the compose $command command."
    }
    $directDockerLines = @(
        $content -split '\r?\n' | Where-Object { $_ -match '^\s*docker\s+compose\b' }
    )
    foreach ($line in $directDockerLines) {
        if ($line -match '\b(config|up|ps)\b') {
            Assert-True (
                $line.Contains('--project-directory') -and
                $line.Contains('--env-file') -and
                $line -match '(?:^|\s)-f(?:\s|$)'
            ) "$Label contains a compose invocation that is not pinned to explicit files."
        }
    }
}

function Invoke-PowerShellGateChecks {
    param([Parameter(Mandatory = $true)][string]$TestRoot)

    $testScripts = Join-Path $TestRoot 'scripts'
    $fakeBin = Join-Path $TestRoot 'fake-bin'
    $dockerLog = Join-Path $TestRoot 'docker-calls.jsonl'
    $envFile = Join-Path $TestRoot '.env'
    $composeFile = Join-Path $TestRoot 'compose.yaml'
    New-Item -ItemType Directory -Path $testScripts, $fakeBin | Out-Null
    Copy-Item -LiteralPath $PowerShellStart -Destination (Join-Path $testScripts 'start.ps1')
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination (Join-Path $TestRoot '.env.example')
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'compose.yaml') -Destination $composeFile
    Set-Content -LiteralPath (Join-Path $fakeBin 'capture.ps1') -Encoding UTF8 -Value @'
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DockerArgs)
[IO.File]::AppendAllText(
    $env:START_GATE_DOCKER_LOG,
    (($DockerArgs | ConvertTo-Json -Compress) + [Environment]::NewLine),
    [Text.UTF8Encoding]::new($false)
)
'@
    Set-Content -LiteralPath (Join-Path $fakeBin 'docker.cmd') -Encoding Ascii -Value @'
@echo off
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0capture.ps1" %*
exit /b %errorlevel%
'@

    $variablesToRestore = $PowerShellBlockedProcessEnvironment + @('PATH', 'START_GATE_DOCKER_LOG')
    $savedEnvironment = @{}
    foreach ($name in $variablesToRestore) {
        $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }

    try {
        foreach ($name in $PowerShellBlockedProcessEnvironment) {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        [Environment]::SetEnvironmentVariable('PATH', "$fakeBin;$($savedEnvironment['PATH'])", 'Process')
        [Environment]::SetEnvironmentVariable('START_GATE_DOCKER_LOG', $dockerLog, 'Process')

        function Invoke-TestStart {
            Remove-Item -LiteralPath $dockerLog -Force -ErrorAction SilentlyContinue
            $previousErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $output = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
                    -File (Join-Path $testScripts 'start.ps1') -ValidateOnly 2>&1
                $exitCode = $LASTEXITCODE
            }
            finally {
                $ErrorActionPreference = $previousErrorActionPreference
            }
            return [pscustomobject]@{
                ExitCode = $exitCode
                Output = @($output)
                Calls = @(Read-DockerCalls $dockerLog)
            }
        }

        Set-SafeDotEnv $envFile
        $result = Invoke-TestStart
        Assert-True ($result.ExitCode -eq 0) 'start.ps1 validate-only rejected a safe non-secret fixture.'
        Assert-ValidateOnlyDockerCall $result.Calls $TestRoot $envFile $composeFile 'start.ps1'

        foreach ($name in $PowerShellBlockedProcessEnvironment) {
            [Environment]::SetEnvironmentVariable($name, 'NON_SECRET_PROCESS_OVERRIDE_SENTINEL', 'Process')
            try {
                $result = Invoke-TestStart
                Assert-True ($result.ExitCode -ne 0) "start.ps1 accepted process override $name."
                Assert-True ($result.Calls.Count -eq 0) "start.ps1 called docker after rejecting process override $name."
                Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_PROCESS_OVERRIDE_SENTINEL'))) `
                    "start.ps1 leaked the process override sentinel for $name."
            }
            finally {
                [Environment]::SetEnvironmentVariable($name, $null, 'Process')
            }
        }

        Set-SafeDotEnv $envFile -Suffix @('COMPOSE_PROFILES=download-execution')
        $result = Invoke-TestStart
        Assert-True ($result.ExitCode -ne 0) 'start.ps1 accepted non-empty COMPOSE_PROFILES from .env.'
        Assert-True ($result.Calls.Count -eq 0) 'start.ps1 called docker after rejecting .env COMPOSE_PROFILES.'

        Set-SafeDotEnv $envFile -Prefix @('COMPOSE_PROFILES=download-execution')
        $result = Invoke-TestStart
        Assert-True ($result.ExitCode -eq 0) 'start.ps1 did not honor an empty final duplicate COMPOSE_PROFILES value.'
        Assert-ValidateOnlyDockerCall $result.Calls $TestRoot $envFile $composeFile 'start.ps1 duplicate empty COMPOSE_PROFILES check'

        Set-SafeDotEnv $envFile -Suffix @('COMPOSE_NONSTANDARD_GATE_SENTINEL=enabled')
        $result = Invoke-TestStart
        Assert-True ($result.ExitCode -ne 0) 'start.ps1 accepted an arbitrary non-empty COMPOSE_* value from .env.'
        Assert-True ($result.Calls.Count -eq 0) 'start.ps1 called docker after rejecting an arbitrary .env COMPOSE_* value.'

        Set-SafeDotEnv $envFile -Prefix @('POSTGRES_PASSWORD=change-me-before-production')
        $result = Invoke-TestStart
        Assert-True ($result.ExitCode -eq 0) 'start.ps1 did not use the last duplicate dotenv value.'
        Assert-ValidateOnlyDockerCall $result.Calls $TestRoot $envFile $composeFile 'start.ps1 duplicate-key check'

        Set-SafeDotEnv $envFile -Suffix @('POSTGRES_PASSWORD=change-me-before-production')
        $result = Invoke-TestStart
        Assert-True ($result.ExitCode -ne 0) 'start.ps1 ignored the unsafe last duplicate dotenv value.'
        Assert-True ($result.Calls.Count -eq 0) 'start.ps1 called docker after the duplicate-key safety check failed.'

        foreach ($unsafePassword in @(
            'POSTGRES_PASSWORD="change-me-before-production"',
            'POSTGRES_PASSWORD=change-me-before-production # NON_SECRET_COMMENT_SENTINEL'
        )) {
            Set-SafeDotEnv $envFile -Suffix @($unsafePassword)
            $result = Invoke-TestStart
            Assert-True ($result.ExitCode -ne 0) 'start.ps1 accepted a quoted or comment-suffixed example PostgreSQL password.'
            Assert-True ($result.Calls.Count -eq 0) 'start.ps1 called docker after a decorated example password was rejected.'
            Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_COMMENT_SENTINEL'))) `
                'start.ps1 leaked a dotenv comment sentinel.'
        }

        foreach ($interpolatedValue in @(
            'AUTH_LOCAL_PASSWORD=${NON_SECRET_INTERPOLATION_SENTINEL}',
            'AUTH_LOCAL_PASSWORD=$NON_SECRET_INTERPOLATION_SENTINEL'
        )) {
            Set-SafeDotEnv $envFile -Suffix @($interpolatedValue)
            $result = Invoke-TestStart
            Assert-True ($result.ExitCode -ne 0) 'start.ps1 accepted dotenv variable interpolation.'
            Assert-True ($result.Calls.Count -eq 0) 'start.ps1 called docker after dotenv interpolation was rejected.'
            Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_INTERPOLATION_SENTINEL'))) `
                'start.ps1 leaked a dotenv interpolation sentinel.'
        }

        Set-SafeDotEnv $envFile
        [Environment]::SetEnvironmentVariable(
            'compose_nonstandard_mixed_case_sentinel',
            'NON_SECRET_PROCESS_OVERRIDE_SENTINEL',
            'Process'
        )
        try {
            $result = Invoke-TestStart
            Assert-True ($result.ExitCode -ne 0) 'start.ps1 accepted a mixed-case COMPOSE_* process override.'
            Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_PROCESS_OVERRIDE_SENTINEL'))) `
                'start.ps1 leaked the mixed-case process override sentinel.'
        }
        finally {
            [Environment]::SetEnvironmentVariable('compose_nonstandard_mixed_case_sentinel', $null, 'Process')
        }
    }
    finally {
        foreach ($name in $variablesToRestore) {
            [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
        }
    }
}

function Read-ShellDockerCalls {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }
    return @(
        Get-Content -LiteralPath $Path -Encoding UTF8 |
            Where-Object { $_.Trim() } |
            ForEach-Object { ,@($_ -split "`t") }
    )
}

function Invoke-ShellGateChecks {
    param([Parameter(Mandatory = $true)][string]$TestRoot)

    $wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wslCommand) {
        throw 'wsl.exe is required to execute the POSIX startup-gate regression checks on Windows.'
    }

    $testScripts = Join-Path $TestRoot 'scripts'
    $fakeBin = Join-Path $TestRoot 'fake-bin'
    $dockerLog = Join-Path $TestRoot 'docker-calls.tsv'
    $envFile = Join-Path $TestRoot '.env'
    New-Item -ItemType Directory -Path $testScripts, $fakeBin | Out-Null
    Copy-Item -LiteralPath $ShellStart -Destination (Join-Path $testScripts 'start.sh')
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination (Join-Path $TestRoot '.env.example')
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'compose.yaml') -Destination (Join-Path $TestRoot 'compose.yaml')
    [IO.File]::WriteAllText(
        (Join-Path $fakeBin 'docker'),
        @'
#!/bin/sh
first=true
for arg in "$@"; do
  if [ "$first" = true ]; then
    first=false
  else
    printf '\t' >> "$START_GATE_DOCKER_LOG"
  fi
  printf '%s' "$arg" >> "$START_GATE_DOCKER_LOG"
done
printf '\n' >> "$START_GATE_DOCKER_LOG"
'@,
        [Text.UTF8Encoding]::new($false)
    )

    $wslRoot = (& wsl.exe -e wslpath -a $TestRoot).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $wslRoot) {
        throw 'Could not translate the temporary startup-gate path for WSL.'
    }
    $wslFakeBin = "$wslRoot/fake-bin"
    $wslDockerLog = "$wslRoot/docker-calls.tsv"
    $wslShellStart = "$wslRoot/scripts/start.sh"
    & wsl.exe -e chmod +x "$wslFakeBin/docker"
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not make the fake WSL docker command executable.'
    }

    function Invoke-TestShellStart {
        param(
            [string]$OverrideName,
            [AllowEmptyString()][string]$OverrideValue
        )

        Remove-Item -LiteralPath $dockerLog -Force -ErrorAction SilentlyContinue
        $arguments = @(
            '-e',
            'env',
            '-i',
            "PATH=${wslFakeBin}:/usr/bin:/bin",
            "START_GATE_DOCKER_LOG=$wslDockerLog"
        )
        if ($PSBoundParameters.ContainsKey('OverrideName')) {
            $arguments += "${OverrideName}=${OverrideValue}"
        }
        $arguments += @('sh', $wslShellStart, '--validate-only')

        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $output = & wsl.exe @arguments 2>&1
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        return [pscustomobject]@{
            ExitCode = $exitCode
            Output = @($output)
            Calls = @(Read-ShellDockerCalls $dockerLog)
        }
    }

    Set-SafeDotEnv $envFile
    $result = Invoke-TestShellStart
    Assert-True ($result.ExitCode -eq 0) 'start.sh --validate-only rejected a safe non-secret fixture.'
    Assert-ValidateOnlyDockerCall `
        $result.Calls `
        $wslRoot `
        "$wslRoot/.env" `
        "$wslRoot/compose.yaml" `
        'start.sh'

    foreach ($name in $BlockedProcessEnvironment) {
        $result = Invoke-TestShellStart $name 'NON_SECRET_PROCESS_OVERRIDE_SENTINEL'
        Assert-True ($result.ExitCode -ne 0) "start.sh accepted process override $name."
        Assert-True ($result.Calls.Count -eq 0) "start.sh called docker after rejecting process override $name."
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_PROCESS_OVERRIDE_SENTINEL'))) `
            "start.sh leaked the process override sentinel for $name."
    }

    $result = Invoke-TestShellStart 'POSTGRES_PASSWORD' ''
    Assert-True ($result.ExitCode -ne 0) 'start.sh accepted an exported empty application process override.'
    Assert-True ($result.Calls.Count -eq 0) 'start.sh called docker after rejecting an empty application process override.'
    $result = Invoke-TestShellStart 'COMPOSE_PROFILES' ''
    Assert-True ($result.ExitCode -ne 0) 'start.sh accepted an exported empty COMPOSE_* process override.'
    Assert-True ($result.Calls.Count -eq 0) 'start.sh called docker after rejecting an empty COMPOSE_* process override.'

    Set-SafeDotEnv $envFile -Prefix @('COMPOSE_PROFILES=download-execution')
    $result = Invoke-TestShellStart
    Assert-True ($result.ExitCode -eq 0) 'start.sh did not honor an empty final duplicate COMPOSE_PROFILES value.'
    Assert-ValidateOnlyDockerCall `
        $result.Calls `
        $wslRoot `
        "$wslRoot/.env" `
        "$wslRoot/compose.yaml" `
        'start.sh duplicate empty COMPOSE_PROFILES check'

    Set-SafeDotEnv $envFile -Suffix @('COMPOSE_PROFILES=download-execution')
    $result = Invoke-TestShellStart
    Assert-True ($result.ExitCode -ne 0) 'start.sh accepted a non-empty final duplicate COMPOSE_PROFILES value.'
    Assert-True ($result.Calls.Count -eq 0) 'start.sh called docker after rejecting .env COMPOSE_PROFILES.'

    Set-SafeDotEnv $envFile -Suffix @('COMPOSE_NONSTANDARD_GATE_SENTINEL=enabled')
    $result = Invoke-TestShellStart
    Assert-True ($result.ExitCode -ne 0) 'start.sh accepted an arbitrary non-empty COMPOSE_* value from .env.'
    Assert-True ($result.Calls.Count -eq 0) 'start.sh called docker after rejecting an arbitrary .env COMPOSE_* value.'

    Set-SafeDotEnv $envFile -Prefix @('POSTGRES_PASSWORD=change-me-before-production')
    $result = Invoke-TestShellStart
    Assert-True ($result.ExitCode -eq 0) 'start.sh did not use the last duplicate dotenv value.'
    Assert-ValidateOnlyDockerCall `
        $result.Calls `
        $wslRoot `
        "$wslRoot/.env" `
        "$wslRoot/compose.yaml" `
        'start.sh duplicate-key check'

    Set-SafeDotEnv $envFile -Suffix @('POSTGRES_PASSWORD=change-me-before-production')
    $result = Invoke-TestShellStart
    Assert-True ($result.ExitCode -ne 0) 'start.sh ignored the unsafe last duplicate dotenv value.'
    Assert-True ($result.Calls.Count -eq 0) 'start.sh called docker after the duplicate-key safety check failed.'

    foreach ($unsafePassword in @(
        'POSTGRES_PASSWORD="change-me-before-production"',
        'POSTGRES_PASSWORD=change-me-before-production # NON_SECRET_COMMENT_SENTINEL'
    )) {
        Set-SafeDotEnv $envFile -Suffix @($unsafePassword)
        $result = Invoke-TestShellStart
        Assert-True ($result.ExitCode -ne 0) 'start.sh accepted a quoted or comment-suffixed example PostgreSQL password.'
        Assert-True ($result.Calls.Count -eq 0) 'start.sh called docker after a decorated example password was rejected.'
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_COMMENT_SENTINEL'))) `
            'start.sh leaked a dotenv comment sentinel.'
    }

    foreach ($interpolatedValue in @(
        'AUTH_LOCAL_PASSWORD=${NON_SECRET_INTERPOLATION_SENTINEL}',
        'AUTH_LOCAL_PASSWORD=$NON_SECRET_INTERPOLATION_SENTINEL'
    )) {
        Set-SafeDotEnv $envFile -Suffix @($interpolatedValue)
        $result = Invoke-TestShellStart
        Assert-True ($result.ExitCode -ne 0) 'start.sh accepted dotenv variable interpolation.'
        Assert-True ($result.Calls.Count -eq 0) 'start.sh called docker after dotenv interpolation was rejected.'
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_INTERPOLATION_SENTINEL'))) `
            'start.sh leaked a dotenv interpolation sentinel.'
    }
}

Assert-StaticComposeContract $PowerShellStart 'start.ps1'
Assert-StaticComposeContract $ShellStart 'start.sh'

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("unin-startup-gates-{0}" -f [Guid]::NewGuid().ToString('N'))
try {
    Invoke-PowerShellGateChecks $tempRoot
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
    Invoke-ShellGateChecks $tempRoot
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}

Write-Host "Startup gate verification passed for $($BlockedProcessEnvironment.Count) blocked process variables." -ForegroundColor Green
