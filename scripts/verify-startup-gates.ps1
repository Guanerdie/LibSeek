$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PowerShellStart = Join-Path $PSScriptRoot 'start.ps1'
$ShellStart = Join-Path $PSScriptRoot 'start.sh'
$PowerShellSecretStart = Join-Path $PSScriptRoot 'start-secrets.ps1'
$ShellSecretStart = Join-Path $PSScriptRoot 'start-secrets.sh'
$DiscoverySecretCompose = Join-Path $ProjectRoot 'deploy/compose.secrets.discovery.yaml.example'
$AvistaZSecretCompose = Join-Path $ProjectRoot 'deploy/compose.secrets.avistaz.yaml.example'
$QbSecretCompose = Join-Path $ProjectRoot 'deploy/compose.secrets.qb.yaml.example'
$DiscoverySecretNames = @(
    'auth_local_username.txt',
    'auth_local_password.txt',
    'auth_session_signing_key.txt',
    'nextfind_username.txt',
    'nextfind_password.txt',
    'tmdb_access_token.txt'
)
$AvistaZSecretNames = @('avistaz_username.txt', 'avistaz_password.txt', 'avistaz_pid.txt')
$QbSecretNames = @('qb_base_url.txt', 'qb_username.txt', 'qb_password.txt')
$AllSecretNames = @($DiscoverySecretNames + $AvistaZSecretNames + $QbSecretNames)
$PlaintextSecretEnvironmentNames = @(
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
$DockerClientEnvironmentNames = @(
    'DOCKER_HOST',
    'DOCKER_CONTEXT',
    'DOCKER_TLS_VERIFY',
    'DOCKER_CERT_PATH',
    'DOCKER_CONFIG'
)
$UnsafeDockerContexts = @(
    '-remote',
    'remote/context',
    'remote context',
    'remote;context',
    ('x' * 129)
)
$UnsupportedDotEnvLines = @(
    'export AUTH_LOCAL_PASSWORD=NON_SECRET_UNSUPPORTED_DOTENV_SENTINEL',
    ' AUTH_LOCAL_PASSWORD = NON_SECRET_UNSUPPORTED_DOTENV_SENTINEL',
    'AUTH_LOCAL_PASSWORD: NON_SECRET_UNSUPPORTED_DOTENV_SENTINEL',
    'auth_local_password=NON_SECRET_UNSUPPORTED_DOTENV_SENTINEL',
    'Auth_Local_Password=NON_SECRET_UNSUPPORTED_DOTENV_SENTINEL'
)

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
$BlockedProcessEnvironment += $DockerClientEnvironmentNames
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
        '# Supported startup-gate comment',
        '  # Supported indented startup-gate comment',
        '',
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

function Set-BootstrapDotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    $safeLines = @(
        'POSTGRES_PASSWORD=NON_SECRET_SAFE_DB_VALUE',
        'DATABASE_URL=postgresql+psycopg://unin:NON_SECRET_SAFE_DB_VALUE@postgres:5432/unin',
        'AUTH_LOCAL_USERNAME=',
        'AUTH_LOCAL_PASSWORD=',
        'AUTH_SESSION_SIGNING_KEY=',
        'COMPOSE_PROFILES='
    )
    foreach ($flag in $DangerousFlags) {
        $safeLines += "${flag}=false"
    }
    [IO.File]::WriteAllLines(
        $Path,
        [string[]]$safeLines,
        [Text.UTF8Encoding]::new($false)
    )
}

function Set-SafeSecretDotEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string[]]$Prefix = @(),
        [string[]]$Suffix = @()
    )

    $safeLines = @(
        '# Supported startup-gate comment',
        '  # Supported indented startup-gate comment',
        '',
        'POSTGRES_PASSWORD=NON_SECRET_SAFE_DB_VALUE',
        'DATABASE_URL=postgresql+psycopg://unin:NON_SECRET_SAFE_DB_VALUE@postgres:5432/unin',
        'AUTH_LOCAL_USERNAME=',
        'AUTH_LOCAL_PASSWORD=',
        'AUTH_SESSION_SIGNING_KEY=',
        'NEXTFIND_USERNAME=',
        'NEXTFIND_PASSWORD=',
        'TMDB_ACCESS_TOKEN=',
        'AVISTAZ_USERNAME=',
        'AVISTAZ_PASSWORD=',
        'AVISTAZ_PID=',
        'QB_BASE_URL=',
        'QB_USERNAME=',
        'QB_PASSWORD=',
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

function New-SyntheticSecretFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string[]]$Names
    )

    $secretRoot = Join-Path $Root 'secrets'
    New-Item -ItemType Directory -Path $secretRoot -Force | Out-Null
    foreach ($name in $Names) {
        [IO.File]::WriteAllText(
            (Join-Path $secretRoot $name),
            'NON_SECRET_FILE_CONTENT_SENTINEL',
            [Text.UTF8Encoding]::new($false)
        )
    }
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
        [string]$Label,
        [string]$ExpectedDockerContext = 'desktop-linux',
        [string[]]$ExpectedProfiles = @(),
        [switch]$FullStart
    )

    $expectedPrefix = @(
        '--context', $ExpectedDockerContext,
        'compose',
        '--project-directory', $ExpectedRoot,
        '--env-file', $ExpectedEnvFile,
        '-f', $ExpectedComposeFile
    )
    foreach ($profile in $ExpectedProfiles) {
        $expectedPrefix += @('--profile', $profile)
    }
    $expectedCalls = @(,@($expectedPrefix + @('config', '--quiet')))
    if ($FullStart) {
        $expectedCalls += ,@($expectedPrefix + @('up', '-d', '--build', '--wait', '--wait-timeout', '180'))
        $expectedCalls += ,@($expectedPrefix + @('ps'))
    }

    Assert-True ($Calls.Count -eq $expectedCalls.Count) `
        "$Label invoked docker $($Calls.Count) times; expected $($expectedCalls.Count)."
    for ($index = 0; $index -lt $expectedCalls.Count; $index++) {
        Assert-True (
            (($Calls[$index] | ConvertTo-Json -Compress) -eq
             ($expectedCalls[$index] | ConvertTo-Json -Compress))
        ) "$Label docker call $($index + 1) did not use the expected compose profile order."
    }
}

function Assert-SecretDockerCalls {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Calls,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedRoot,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedEnvFile,
        [Parameter(Mandatory = $true)]
        [string[]]$ExpectedComposeFiles,
        [string[]]$ExpectedProfiles = @(),
        [switch]$FullStart,
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [string]$ExpectedDockerContext = 'desktop-linux'
    )

    $expectedPrefix = @(
        '--context', $ExpectedDockerContext,
        'compose',
        '--project-directory', $ExpectedRoot,
        '--env-file', $ExpectedEnvFile
    )
    foreach ($path in $ExpectedComposeFiles) {
        $expectedPrefix += @('-f', $path)
    }
    foreach ($name in $ExpectedProfiles) {
        $expectedPrefix += @('--profile', $name)
    }

    $expectedCalls = @(
        ,@($expectedPrefix + @('config', '--quiet'))
    )
    if ($FullStart) {
        $expectedCalls += ,@($expectedPrefix + @('up', '-d', '--build', '--wait', '--wait-timeout', '180'))
        $expectedCalls += ,@($expectedPrefix + @('ps'))
    }
    Assert-True ($Calls.Count -eq $expectedCalls.Count) `
        "$Label invoked docker $($Calls.Count) times; expected $($expectedCalls.Count)."
    for ($index = 0; $index -lt $expectedCalls.Count; $index++) {
        Assert-True (
            (($Calls[$index] | ConvertTo-Json -Compress) -eq
             ($expectedCalls[$index] | ConvertTo-Json -Compress))
        ) "$Label docker call $($index + 1) did not use the fixed compose/profile order."
    }
}

function Assert-StaticComposeContract {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $content = Get-Content -Raw -LiteralPath $Path -Encoding UTF8
    Assert-True ($content.Contains('--context')) "$Label does not pass an explicit Docker context."
    Assert-True ($content.Contains('--env-file')) "$Label does not pass --env-file."
    Assert-True (
        $content -match '(?m)(?:^|\s)-f(?:\s|$)' -or
        $content.Contains("'-f'") -or
        $content.Contains('"-f"')
    ) "$Label does not pass -f."
    foreach ($command in @('config', 'up', 'ps')) {
        Assert-True ($content -match "(?m)\b$command\b") "$Label does not contain the compose $command command."
    }
    $dockerCommandLines = @(
        $content -split '\r?\n' |
            Where-Object { $_ -match '^\s*(?:if\s+!\s+|&\s+)?docker\b' }
    )
    Assert-True ($dockerCommandLines.Count -gt 0) "$Label does not invoke docker."
    $indirectContextContract = $content -match '(?s)--context.{0,256}\bcompose\b'
    foreach ($line in $dockerCommandLines) {
        if ($line -match '\b(config|up|ps)\b') {
            if ($line -match '\bdocker\s+--context\b.*\bcompose\b') {
                Assert-True (
                    $line.Contains('--project-directory') -and
                    $line.Contains('--env-file') -and
                    $line -match '(?:^|\s)-f(?:\s|$)'
                ) "$Label contains a direct compose invocation that is not pinned to explicit files."
            }
            else {
                $usesPinnedArguments =
                    $line.Contains('@composeArguments') -or $line.Contains('"$@"')
                Assert-True ($usesPinnedArguments -and $indirectContextContract) `
                    "$Label contains an indirect compose invocation without pinned context arguments."
            }
        }
    }
}

function Assert-SecretFilesExistenceOnlyContract {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [switch]$PowerShell
    )

    $secretPathLines = @(
        Get-Content -LiteralPath $Path -Encoding UTF8 |
            Where-Object { $_ -match '(?i)(?:[\x27"]|[/\\])secrets(?:[/\\\x27"])' }
    )
    Assert-True ($secretPathLines.Count -gt 0) "$Label does not check selected Secret paths."
    foreach ($line in $secretPathLines) {
        $existenceOnly = if ($PowerShell) {
            $line -match '\bTest-Path\b.*-PathType\s+Leaf\b'
        }
        else {
            $line -match '\[\s+!\s+-f\s+'
        }
        Assert-True $existenceOnly `
            "$Label may only test selected Secret file existence; it must not read Secret contents."
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
[IO.File]::AppendAllText(
    $env:START_GATE_DOCKER_LOG,
    (($args | ConvertTo-Json -Compress) + [Environment]::NewLine),
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
            param(
                [string]$DockerContext = 'desktop-linux',
                [switch]$OmitDockerContext,
                [switch]$FullStart
            )

            Remove-Item -LiteralPath $dockerLog -Force -ErrorAction SilentlyContinue
            $arguments = @(
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy', 'Bypass',
                '-File', (Join-Path $testScripts 'start.ps1')
            )
            if (-not $OmitDockerContext) {
                $arguments += @('-DockerContext', $DockerContext)
            }
            if (-not $FullStart) {
                $arguments += '-ValidateOnly'
            }
            $previousErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $output = & powershell.exe @arguments 2>&1
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

        Set-SafeDotEnv $envFile -Suffix @('ENABLE_DOWNLOAD_EXECUTOR=true')
        $result = Invoke-TestStart
        Assert-True ($result.ExitCode -eq 0) 'start.ps1 rejected ENABLE_DOWNLOAD_EXECUTOR=true.'
        Assert-ValidateOnlyDockerCall `
            $result.Calls $TestRoot $envFile $composeFile 'start.ps1 download executor profile' `
            -ExpectedProfiles @('download-execution')

        Set-SafeDotEnv $envFile -Suffix @('ENABLE_DOWNLOAD_MONITOR=true')
        $result = Invoke-TestStart
        Assert-True ($result.ExitCode -eq 0) 'start.ps1 rejected ENABLE_DOWNLOAD_MONITOR=true.'
        Assert-ValidateOnlyDockerCall `
            $result.Calls $TestRoot $envFile $composeFile 'start.ps1 download monitor profile' `
            -ExpectedProfiles @('download-monitor')

        Set-SafeDotEnv $envFile -Suffix @(
            'ENABLE_DOWNLOAD_EXECUTOR=true',
            'ENABLE_DOWNLOAD_MONITOR=true'
        )
        $result = Invoke-TestStart -FullStart
        Assert-True ($result.ExitCode -eq 0) 'start.ps1 rejected both download service switches.'
        Assert-ValidateOnlyDockerCall `
            $result.Calls $TestRoot $envFile $composeFile 'start.ps1 download service profiles' `
            -ExpectedProfiles @('download-execution', 'download-monitor') `
            -FullStart
        Set-SafeDotEnv $envFile

        Set-BootstrapDotEnv $envFile
        $result = Invoke-TestStart
        Assert-True ($result.ExitCode -eq 0) 'start.ps1 rejected browser-based administrator bootstrap.'
        Assert-ValidateOnlyDockerCall $result.Calls $TestRoot $envFile $composeFile 'start.ps1 browser bootstrap'

        Remove-Item -LiteralPath $envFile -Force
        $result = Invoke-TestStart
        Assert-True ($result.ExitCode -eq 0) 'start.ps1 did not initialize a missing local .env.'
        Assert-True (Test-Path -LiteralPath $envFile -PathType Leaf) `
            'start.ps1 did not create .env.'
        $generatedDotEnv = Get-Content -Raw -LiteralPath $envFile -Encoding UTF8
        Assert-True (-not $generatedDotEnv.Contains('change-me-before-production')) `
            'start.ps1 retained the example PostgreSQL password.'
        Assert-True ($generatedDotEnv.Contains("AUTH_LOCAL_USERNAME=`n")) `
            'start.ps1 preconfigured a local administrator instead of using browser setup.'
        Assert-ValidateOnlyDockerCall $result.Calls $TestRoot $envFile $composeFile 'start.ps1 first run'
        Set-SafeDotEnv $envFile

        $result = Invoke-TestStart -OmitDockerContext
        Assert-True ($result.ExitCode -ne 0) 'start.ps1 accepted a missing Docker context.'
        Assert-True ($result.Calls.Count -eq 0) 'start.ps1 called docker without a Docker context.'
        foreach ($unsafeContext in $UnsafeDockerContexts) {
            $result = Invoke-TestStart -DockerContext $unsafeContext
            Assert-True ($result.ExitCode -ne 0) 'start.ps1 accepted an unsafe Docker context name.'
            Assert-True ($result.Calls.Count -eq 0) `
                'start.ps1 called docker with an unsafe Docker context name.'
        }

        Set-SafeDotEnv $envFile -Suffix @(
            'export EARLY_DOTENV_GATE=NON_SECRET_EARLY_DOTENV_SENTINEL'
        )
        foreach ($name in @($DockerClientEnvironmentNames + 'AUTH_LOCAL_USERNAME' + 'COMPOSE_FILE')) {
            [Environment]::SetEnvironmentVariable(
                $name,
                'NON_SECRET_EARLY_AMBIENT_SENTINEL',
                'Process'
            )
            try {
                $result = Invoke-TestStart
                $combinedOutput = $result.Output -join "`n"
                Assert-True ($result.ExitCode -ne 0) `
                    "start.ps1 accepted early ambient override $name."
                Assert-True ($result.Calls.Count -eq 0) `
                    "start.ps1 called docker after early ambient override $name."
                Assert-True ($combinedOutput.Contains($name)) `
                    "start.ps1 read .env before rejecting early ambient override $name."
                Assert-True (-not $combinedOutput.Contains('NON_SECRET_EARLY_DOTENV_SENTINEL')) `
                    'start.ps1 leaked the early dotenv sentinel.'
                Assert-True (-not $combinedOutput.Contains('NON_SECRET_EARLY_AMBIENT_SENTINEL')) `
                    'start.ps1 leaked the early ambient sentinel.'
            }
            finally {
                [Environment]::SetEnvironmentVariable($name, $null, 'Process')
            }
        }
        Set-SafeDotEnv $envFile

        foreach ($unsupportedLine in $UnsupportedDotEnvLines) {
            Set-SafeDotEnv $envFile -Suffix @($unsupportedLine)
            $result = Invoke-TestStart
            Assert-True ($result.ExitCode -ne 0) 'start.ps1 accepted unsupported dotenv syntax.'
            Assert-True ($result.Calls.Count -eq 0) `
                'start.ps1 called docker after rejecting unsupported dotenv syntax.'
            Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_UNSUPPORTED_DOTENV_SENTINEL'))) `
                'start.ps1 leaked the value from an unsupported dotenv entry.'
        }
        Set-SafeDotEnv $envFile

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

function Invoke-PowerShellSecretGateChecks {
    param([Parameter(Mandatory = $true)][string]$TestRoot)

    $testScripts = Join-Path $TestRoot 'scripts'
    $testDeploy = Join-Path $TestRoot 'deploy'
    $fakeBin = Join-Path $TestRoot 'fake-bin'
    $dockerLog = Join-Path $TestRoot 'docker-calls.jsonl'
    $envFile = Join-Path $TestRoot '.env'
    $composeFile = Join-Path $TestRoot 'compose.yaml'
    $discoveryCompose = Join-Path $testDeploy 'compose.secrets.discovery.yaml.example'
    $avistazCompose = Join-Path $testDeploy 'compose.secrets.avistaz.yaml.example'
    $qbCompose = Join-Path $testDeploy 'compose.secrets.qb.yaml.example'
    New-Item -ItemType Directory -Path $testScripts, $testDeploy, $fakeBin | Out-Null
    Copy-Item -LiteralPath $PowerShellSecretStart -Destination (Join-Path $testScripts 'start-secrets.ps1')
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination (Join-Path $TestRoot '.env.example')
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'compose.yaml') -Destination $composeFile
    Copy-Item -LiteralPath $DiscoverySecretCompose -Destination $discoveryCompose
    Copy-Item -LiteralPath $AvistaZSecretCompose -Destination $avistazCompose
    Copy-Item -LiteralPath $QbSecretCompose -Destination $qbCompose
    Set-Content -LiteralPath (Join-Path $fakeBin 'capture.ps1') -Encoding UTF8 -Value @'
[IO.File]::AppendAllText(
    $env:START_GATE_DOCKER_LOG,
    (($args | ConvertTo-Json -Compress) + [Environment]::NewLine),
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

        function Invoke-TestSecretStart {
            param(
                [string[]]$Arguments = @(),
                [string]$DockerContext = 'desktop-linux',
                [switch]$OmitDockerContext
            )

            Remove-Item -LiteralPath $dockerLog -Force -ErrorAction SilentlyContinue
            $scriptArguments = @(
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy', 'Bypass',
                '-File', (Join-Path $testScripts 'start-secrets.ps1')
            )
            if (-not $OmitDockerContext) {
                $scriptArguments += @('-DockerContext', $DockerContext)
            }
            $scriptArguments += $Arguments
            $previousErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $output = & powershell.exe @scriptArguments 2>&1
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

        Set-SafeSecretDotEnv $envFile
        New-SyntheticSecretFiles $TestRoot $DiscoverySecretNames
        $dotenvBefore = Get-Content -Raw -LiteralPath $envFile -Encoding UTF8
        $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
        Assert-True ($result.ExitCode -eq 0) `
            'start-secrets.ps1 rejected empty plaintext credentials with the discovery Secret layer present.'
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_FILE_CONTENT_SENTINEL'))) `
            'start-secrets.ps1 printed synthetic Secret file contents.'
        Assert-True ((Get-Content -Raw -LiteralPath $envFile -Encoding UTF8) -ceq $dotenvBefore) `
            'start-secrets.ps1 modified .env or enabled a setting by default.'
        Assert-SecretDockerCalls `
            $result.Calls `
            $TestRoot `
            $envFile `
            @($composeFile, $discoveryCompose) `
            -Label 'start-secrets.ps1 discovery validation'

        $result = Invoke-TestSecretStart @('-Discovery')
        if ($result.ExitCode -ne 0) {
            $safeDiagnostic = (($result.Output -join "`n") -replace 'NON_SECRET_FILE_CONTENT_SENTINEL', '[REDACTED]')
            throw "start-secrets.ps1 rejected an initial safe synthetic full startup: $safeDiagnostic"
        }
        Assert-SecretDockerCalls `
            $result.Calls `
            $TestRoot `
            $envFile `
            @($composeFile, $discoveryCompose) `
            -FullStart `
            -Label 'start-secrets.ps1 initial synthetic full startup'

        $result = Invoke-TestSecretStart `
            -Arguments @('-Discovery', '-ValidateOnly') `
            -OmitDockerContext
        Assert-True ($result.ExitCode -ne 0) `
            'start-secrets.ps1 accepted a missing Docker context.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start-secrets.ps1 called docker without a Docker context.'
        foreach ($unsafeContext in $UnsafeDockerContexts) {
            $result = Invoke-TestSecretStart `
                -Arguments @('-Discovery', '-ValidateOnly') `
                -DockerContext $unsafeContext
            Assert-True ($result.ExitCode -ne 0) `
                'start-secrets.ps1 accepted an unsafe Docker context name.'
            Assert-True ($result.Calls.Count -eq 0) `
                'start-secrets.ps1 called docker with an unsafe Docker context name.'
        }

        Set-SafeSecretDotEnv $envFile -Suffix @(
            'export EARLY_DOTENV_GATE=NON_SECRET_EARLY_DOTENV_SENTINEL'
        )
        foreach ($name in @($DockerClientEnvironmentNames + 'AUTH_LOCAL_USERNAME' + 'COMPOSE_FILE')) {
            [Environment]::SetEnvironmentVariable(
                $name,
                'NON_SECRET_EARLY_AMBIENT_SENTINEL',
                'Process'
            )
            try {
                $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
                $combinedOutput = $result.Output -join "`n"
                Assert-True ($result.ExitCode -ne 0) `
                    "start-secrets.ps1 accepted early ambient override $name."
                Assert-True ($result.Calls.Count -eq 0) `
                    "start-secrets.ps1 called docker after early ambient override $name."
                Assert-True ($combinedOutput.Contains($name)) `
                    "start-secrets.ps1 read .env before rejecting early ambient override $name."
                Assert-True (-not $combinedOutput.Contains('NON_SECRET_EARLY_DOTENV_SENTINEL')) `
                    'start-secrets.ps1 leaked the early dotenv sentinel.'
                Assert-True (-not $combinedOutput.Contains('NON_SECRET_EARLY_AMBIENT_SENTINEL')) `
                    'start-secrets.ps1 leaked the early ambient sentinel.'
            }
            finally {
                [Environment]::SetEnvironmentVariable($name, $null, 'Process')
            }
        }
        Set-SafeSecretDotEnv $envFile

        foreach ($unsupportedLine in $UnsupportedDotEnvLines) {
            Set-SafeSecretDotEnv $envFile -Suffix @($unsupportedLine)
            $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
            Assert-True ($result.ExitCode -ne 0) `
                'start-secrets.ps1 accepted unsupported dotenv syntax.'
            Assert-True ($result.Calls.Count -eq 0) `
                'start-secrets.ps1 called docker after rejecting unsupported dotenv syntax.'
            Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_UNSUPPORTED_DOTENV_SENTINEL'))) `
                'start-secrets.ps1 leaked the value from an unsupported dotenv entry.'
        }
        Set-SafeSecretDotEnv $envFile

        Remove-Item -LiteralPath $envFile -Force
        $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
        Assert-True ($result.ExitCode -ne 0) 'start-secrets.ps1 accepted a missing .env.'
        Assert-True ($result.Calls.Count -eq 0) 'start-secrets.ps1 called docker without .env.'
        Assert-True (-not (Test-Path -LiteralPath $envFile)) 'start-secrets.ps1 created a missing .env.'
        Set-SafeSecretDotEnv $envFile

        Remove-Item -LiteralPath (Join-Path $TestRoot 'secrets/tmdb_access_token.txt') -Force
        $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
        Assert-True ($result.ExitCode -ne 0) 'start-secrets.ps1 accepted a missing selected Secret file.'
        Assert-True ($result.Calls.Count -eq 0) 'start-secrets.ps1 called docker with a missing selected Secret file.'
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_FILE_CONTENT_SENTINEL'))) `
            'start-secrets.ps1 printed Secret contents while reporting a missing file.'
        New-SyntheticSecretFiles $TestRoot @('tmdb_access_token.txt')

        foreach ($arguments in @(
            ,@('-Discovery', '-AvistaZ', '-ValidateOnly'),
            ,@('-Discovery', '-Qb', '-ValidateOnly')
        )) {
            $result = Invoke-TestSecretStart $arguments
            Assert-True ($result.ExitCode -ne 0) `
                'start-secrets.ps1 accepted a selected layer whose Secret files were missing.'
            Assert-True ($result.Calls.Count -eq 0) `
                'start-secrets.ps1 called docker with missing optional-layer Secret files.'
        }

        foreach ($name in $PowerShellBlockedProcessEnvironment) {
            [Environment]::SetEnvironmentVariable($name, 'NON_SECRET_PROCESS_OVERRIDE_SENTINEL', 'Process')
            try {
                $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
                Assert-True ($result.ExitCode -ne 0) "start-secrets.ps1 accepted process override $name."
                Assert-True ($result.Calls.Count -eq 0) `
                    "start-secrets.ps1 called docker after rejecting process override $name."
                Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_PROCESS_OVERRIDE_SENTINEL'))) `
                    "start-secrets.ps1 leaked the process override sentinel for $name."
            }
            finally {
                [Environment]::SetEnvironmentVariable($name, $null, 'Process')
            }
        }

        Set-SafeSecretDotEnv $envFile -Suffix @('COMPOSE_PROFILES=download-execution')
        $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
        Assert-True ($result.ExitCode -ne 0) 'start-secrets.ps1 accepted non-empty COMPOSE_PROFILES from .env.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start-secrets.ps1 called docker after rejecting .env COMPOSE_PROFILES.'

        Set-SafeSecretDotEnv $envFile -Suffix @('AUTH_LOCAL_PASSWORD=${NON_SECRET_INTERPOLATION_SENTINEL}')
        $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
        Assert-True ($result.ExitCode -ne 0) 'start-secrets.ps1 accepted dotenv variable interpolation.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start-secrets.ps1 called docker after dotenv interpolation was rejected.'
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_INTERPOLATION_SENTINEL'))) `
            'start-secrets.ps1 leaked a dotenv interpolation sentinel.'

        foreach ($name in $PlaintextSecretEnvironmentNames) {
            Set-SafeSecretDotEnv $envFile -Suffix @("${name}=NON_SECRET_PLAINTEXT_CREDENTIAL_SENTINEL")
            $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
            Assert-True ($result.ExitCode -ne 0) `
                "start-secrets.ps1 accepted plaintext credential $name in .env."
            Assert-True ($result.Calls.Count -eq 0) `
                "start-secrets.ps1 called docker after rejecting plaintext credential $name."
            Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_PLAINTEXT_CREDENTIAL_SENTINEL'))) `
                "start-secrets.ps1 leaked plaintext credential $name."
        }
        Set-SafeSecretDotEnv $envFile -Prefix @(
            'AUTH_LOCAL_PASSWORD=NON_SECRET_PLAINTEXT_CREDENTIAL_SENTINEL'
        )
        $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
        Assert-True ($result.ExitCode -ne 0) `
            'start-secrets.ps1 accepted an earlier non-empty duplicate plaintext credential.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start-secrets.ps1 called docker after an earlier plaintext credential was rejected.'
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_PLAINTEXT_CREDENTIAL_SENTINEL'))) `
            'start-secrets.ps1 leaked an earlier duplicate plaintext credential.'

        Set-SafeSecretDotEnv $envFile -Suffix @('POSTGRES_PASSWORD=change-me-before-production')
        $result = Invoke-TestSecretStart @('-Discovery', '-ValidateOnly')
        Assert-True ($result.ExitCode -ne 0) 'start-secrets.ps1 accepted the example PostgreSQL password.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start-secrets.ps1 called docker after rejecting the example PostgreSQL password.'

        Set-SafeSecretDotEnv $envFile
        foreach ($arguments in @(
            ,@('-ValidateOnly'),
            ,@('-Discovery', '-Profile', 'automation-preflight', '-ValidateOnly'),
            ,@('-Discovery', '-Profile', 'download-monitor', '-ValidateOnly'),
            ,@('-Discovery', '-Qb', '-Profile', 'download-execution', '-ValidateOnly'),
            ,@('-Discovery', '-Profile', 'unsupported-profile', '-ValidateOnly')
        )) {
            $result = Invoke-TestSecretStart $arguments
            Assert-True ($result.ExitCode -ne 0) `
                'start-secrets.ps1 accepted a missing layer dependency or unsupported profile.'
            Assert-True ($result.Calls.Count -eq 0) `
                'start-secrets.ps1 called docker after rejecting a layer/profile selection.'
        }

        New-SyntheticSecretFiles $TestRoot @($AvistaZSecretNames + $QbSecretNames)
        $result = Invoke-TestSecretStart @(
            '-Discovery',
            '-Qb',
            '-AvistaZ',
            '-Profile', 'download-monitor,automation-preflight,download-execution',
            '-ValidateOnly'
        )
        Assert-True ($result.ExitCode -eq 0) 'start-secrets.ps1 rejected a valid all-layer/profile selection.'
        Assert-SecretDockerCalls `
            $result.Calls `
            $TestRoot `
            $envFile `
            @($composeFile, $discoveryCompose, $avistazCompose, $qbCompose) `
            @('automation-preflight', 'download-execution', 'download-monitor') `
            -Label 'start-secrets.ps1 fixed layer/profile order'

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
            [AllowEmptyString()][string]$OverrideValue,
            [string]$DockerContext = 'default',
            [switch]$OmitDockerContext,
            [switch]$FullStart
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
        $arguments += @('sh', $wslShellStart)
        if (-not $OmitDockerContext) {
            $arguments += @('--docker-context', $DockerContext)
        }
        if (-not $FullStart) {
            $arguments += '--validate-only'
        }

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
        'start.sh' `
        -ExpectedDockerContext 'default'

    Set-SafeDotEnv $envFile -Suffix @('ENABLE_DOWNLOAD_EXECUTOR=true')
    $result = Invoke-TestShellStart
    Assert-True ($result.ExitCode -eq 0) 'start.sh rejected ENABLE_DOWNLOAD_EXECUTOR=true.'
    Assert-ValidateOnlyDockerCall `
        $result.Calls `
        $wslRoot `
        "$wslRoot/.env" `
        "$wslRoot/compose.yaml" `
        'start.sh download executor profile' `
        -ExpectedDockerContext 'default' `
        -ExpectedProfiles @('download-execution')

    Set-SafeDotEnv $envFile -Suffix @('ENABLE_DOWNLOAD_MONITOR=true')
    $result = Invoke-TestShellStart
    Assert-True ($result.ExitCode -eq 0) 'start.sh rejected ENABLE_DOWNLOAD_MONITOR=true.'
    Assert-ValidateOnlyDockerCall `
        $result.Calls `
        $wslRoot `
        "$wslRoot/.env" `
        "$wslRoot/compose.yaml" `
        'start.sh download monitor profile' `
        -ExpectedDockerContext 'default' `
        -ExpectedProfiles @('download-monitor')

    Set-SafeDotEnv $envFile -Suffix @(
        'ENABLE_DOWNLOAD_EXECUTOR=true',
        'ENABLE_DOWNLOAD_MONITOR=true'
    )
    $result = Invoke-TestShellStart -FullStart
    Assert-True ($result.ExitCode -eq 0) 'start.sh rejected both download service switches.'
    Assert-ValidateOnlyDockerCall `
        $result.Calls `
        $wslRoot `
        "$wslRoot/.env" `
        "$wslRoot/compose.yaml" `
        'start.sh download service profiles' `
        -ExpectedDockerContext 'default' `
        -ExpectedProfiles @('download-execution', 'download-monitor') `
        -FullStart
    Set-SafeDotEnv $envFile

    Set-BootstrapDotEnv $envFile
    $result = Invoke-TestShellStart
    Assert-True ($result.ExitCode -eq 0) 'start.sh rejected browser-based administrator bootstrap.'
    Assert-ValidateOnlyDockerCall `
        $result.Calls `
        $wslRoot `
        "$wslRoot/.env" `
        "$wslRoot/compose.yaml" `
        'start.sh browser bootstrap' `
        -ExpectedDockerContext 'default'

    Remove-Item -LiteralPath $envFile -Force
    $result = Invoke-TestShellStart
    Assert-True ($result.ExitCode -eq 0) 'start.sh did not initialize a missing local .env.'
    Assert-True (Test-Path -LiteralPath $envFile -PathType Leaf) `
        'start.sh did not create .env.'
    $generatedDotEnv = Get-Content -Raw -LiteralPath $envFile -Encoding UTF8
    Assert-True (-not $generatedDotEnv.Contains('change-me-before-production')) `
        'start.sh retained the example PostgreSQL password.'
    Assert-True ($generatedDotEnv.Contains("AUTH_LOCAL_USERNAME=`n")) `
        'start.sh preconfigured a local administrator instead of using browser setup.'
    Assert-ValidateOnlyDockerCall `
        $result.Calls `
        $wslRoot `
        "$wslRoot/.env" `
        "$wslRoot/compose.yaml" `
        'start.sh first run' `
        -ExpectedDockerContext 'default'
    Set-SafeDotEnv $envFile

    $result = Invoke-TestShellStart -OmitDockerContext
    Assert-True ($result.ExitCode -ne 0) 'start.sh accepted a missing Docker context.'
    Assert-True ($result.Calls.Count -eq 0) 'start.sh called docker without a Docker context.'
    foreach ($unsafeContext in $UnsafeDockerContexts) {
        $result = Invoke-TestShellStart -DockerContext $unsafeContext
        Assert-True ($result.ExitCode -ne 0) 'start.sh accepted an unsafe Docker context name.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start.sh called docker with an unsafe Docker context name.'
    }

    Set-SafeDotEnv $envFile -Suffix @(
        'export EARLY_DOTENV_GATE=NON_SECRET_EARLY_DOTENV_SENTINEL'
    )
    foreach ($name in @($DockerClientEnvironmentNames + 'AUTH_LOCAL_USERNAME' + 'COMPOSE_FILE')) {
        $result = Invoke-TestShellStart $name 'NON_SECRET_EARLY_AMBIENT_SENTINEL'
        $combinedOutput = $result.Output -join "`n"
        Assert-True ($result.ExitCode -ne 0) `
            "start.sh accepted early ambient override $name."
        Assert-True ($result.Calls.Count -eq 0) `
            "start.sh called docker after early ambient override $name."
        Assert-True ($combinedOutput.Contains($name)) `
            "start.sh read .env before rejecting early ambient override $name."
        Assert-True (-not $combinedOutput.Contains('NON_SECRET_EARLY_DOTENV_SENTINEL')) `
            'start.sh leaked the early dotenv sentinel.'
        Assert-True (-not $combinedOutput.Contains('NON_SECRET_EARLY_AMBIENT_SENTINEL')) `
            'start.sh leaked the early ambient sentinel.'
    }
    Set-SafeDotEnv $envFile

    foreach ($unsupportedLine in $UnsupportedDotEnvLines) {
        Set-SafeDotEnv $envFile -Suffix @($unsupportedLine)
        $result = Invoke-TestShellStart
        Assert-True ($result.ExitCode -ne 0) 'start.sh accepted unsupported dotenv syntax.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start.sh called docker after rejecting unsupported dotenv syntax.'
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_UNSUPPORTED_DOTENV_SENTINEL'))) `
            'start.sh leaked the value from an unsupported dotenv entry.'
    }
    Set-SafeDotEnv $envFile

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
        'start.sh duplicate empty COMPOSE_PROFILES check' `
        -ExpectedDockerContext 'default'

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
        'start.sh duplicate-key check' `
        -ExpectedDockerContext 'default'

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

function Invoke-ShellSecretGateChecks {
    param([Parameter(Mandatory = $true)][string]$TestRoot)

    $wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wslCommand) {
        throw 'wsl.exe is required to execute the POSIX Secret startup-gate regression checks on Windows.'
    }

    $testScripts = Join-Path $TestRoot 'scripts'
    $testDeploy = Join-Path $TestRoot 'deploy'
    $fakeBin = Join-Path $TestRoot 'fake-bin'
    $dockerLog = Join-Path $TestRoot 'docker-calls.tsv'
    $envFile = Join-Path $TestRoot '.env'
    New-Item -ItemType Directory -Path $testScripts, $testDeploy, $fakeBin | Out-Null
    Copy-Item -LiteralPath $ShellSecretStart -Destination (Join-Path $testScripts 'start-secrets.sh')
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination (Join-Path $TestRoot '.env.example')
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'compose.yaml') -Destination (Join-Path $TestRoot 'compose.yaml')
    Copy-Item -LiteralPath $DiscoverySecretCompose -Destination `
        (Join-Path $testDeploy 'compose.secrets.discovery.yaml.example')
    Copy-Item -LiteralPath $AvistaZSecretCompose -Destination `
        (Join-Path $testDeploy 'compose.secrets.avistaz.yaml.example')
    Copy-Item -LiteralPath $QbSecretCompose -Destination `
        (Join-Path $testDeploy 'compose.secrets.qb.yaml.example')
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
        throw 'Could not translate the temporary Secret startup-gate path for WSL.'
    }
    $wslFakeBin = "$wslRoot/fake-bin"
    $wslDockerLog = "$wslRoot/docker-calls.tsv"
    $wslShellStart = "$wslRoot/scripts/start-secrets.sh"
    $wslEnvFile = "$wslRoot/.env"
    $wslComposeFile = "$wslRoot/compose.yaml"
    $wslDiscoveryCompose = "$wslRoot/deploy/compose.secrets.discovery.yaml.example"
    $wslAvistaZCompose = "$wslRoot/deploy/compose.secrets.avistaz.yaml.example"
    $wslQbCompose = "$wslRoot/deploy/compose.secrets.qb.yaml.example"
    & wsl.exe -e chmod +x "$wslFakeBin/docker"
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not make the fake WSL docker command executable.'
    }

    function Invoke-TestShellSecretStart {
        param(
            [string[]]$ScriptArguments = @(),
            [string]$OverrideName,
            [AllowEmptyString()][string]$OverrideValue,
            [string]$DockerContext = 'default',
            [switch]$OmitDockerContext
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
        $arguments += @('sh', $wslShellStart)
        if (-not $OmitDockerContext) {
            $arguments += @('--docker-context', $DockerContext)
        }
        $arguments += $ScriptArguments

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

    Set-SafeSecretDotEnv $envFile
    New-SyntheticSecretFiles $TestRoot $DiscoverySecretNames
    $dotenvBefore = Get-Content -Raw -LiteralPath $envFile -Encoding UTF8
    $result = Invoke-TestShellSecretStart @('--discovery', '--validate-only')
    Assert-True ($result.ExitCode -eq 0) `
        'start-secrets.sh rejected empty plaintext credentials with the discovery Secret layer present.'
    Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_FILE_CONTENT_SENTINEL'))) `
        'start-secrets.sh printed synthetic Secret file contents.'
    Assert-True ((Get-Content -Raw -LiteralPath $envFile -Encoding UTF8) -ceq $dotenvBefore) `
        'start-secrets.sh modified .env or enabled a setting by default.'
    Assert-SecretDockerCalls `
        $result.Calls `
        $wslRoot `
        $wslEnvFile `
        @($wslComposeFile, $wslDiscoveryCompose) `
        -Label 'start-secrets.sh discovery validation' `
        -ExpectedDockerContext 'default'

    $result = Invoke-TestShellSecretStart `
        -ScriptArguments @('--discovery', '--validate-only') `
        -OmitDockerContext
    Assert-True ($result.ExitCode -ne 0) 'start-secrets.sh accepted a missing Docker context.'
    Assert-True ($result.Calls.Count -eq 0) `
        'start-secrets.sh called docker without a Docker context.'
    foreach ($unsafeContext in $UnsafeDockerContexts) {
        $result = Invoke-TestShellSecretStart `
            -ScriptArguments @('--discovery', '--validate-only') `
            -DockerContext $unsafeContext
        Assert-True ($result.ExitCode -ne 0) `
            'start-secrets.sh accepted an unsafe Docker context name.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start-secrets.sh called docker with an unsafe Docker context name.'
    }

    Set-SafeSecretDotEnv $envFile -Suffix @(
        'export EARLY_DOTENV_GATE=NON_SECRET_EARLY_DOTENV_SENTINEL'
    )
    foreach ($name in @($DockerClientEnvironmentNames + 'AUTH_LOCAL_USERNAME' + 'COMPOSE_FILE')) {
        $result = Invoke-TestShellSecretStart `
            -ScriptArguments @('--discovery', '--validate-only') `
            -OverrideName $name `
            -OverrideValue 'NON_SECRET_EARLY_AMBIENT_SENTINEL'
        $combinedOutput = $result.Output -join "`n"
        Assert-True ($result.ExitCode -ne 0) `
            "start-secrets.sh accepted early ambient override $name."
        Assert-True ($result.Calls.Count -eq 0) `
            "start-secrets.sh called docker after early ambient override $name."
        Assert-True ($combinedOutput.Contains($name)) `
            "start-secrets.sh read .env before rejecting early ambient override $name."
        Assert-True (-not $combinedOutput.Contains('NON_SECRET_EARLY_DOTENV_SENTINEL')) `
            'start-secrets.sh leaked the early dotenv sentinel.'
        Assert-True (-not $combinedOutput.Contains('NON_SECRET_EARLY_AMBIENT_SENTINEL')) `
            'start-secrets.sh leaked the early ambient sentinel.'
    }
    Set-SafeSecretDotEnv $envFile

    $result = Invoke-TestShellSecretStart -ScriptArguments @('--unsupported-option')
    Assert-True ($result.ExitCode -ne 0) 'start-secrets.sh accepted an unsupported option.'
    Assert-True ($result.Calls.Count -eq 0) `
        'start-secrets.sh called docker after rejecting an unsupported option.'
    Assert-True (($result.Output -join "`n").Contains('--docker-context')) `
        'start-secrets.sh usage omitted the required --docker-context option.'

    foreach ($unsupportedLine in $UnsupportedDotEnvLines) {
        Set-SafeSecretDotEnv $envFile -Suffix @($unsupportedLine)
        $result = Invoke-TestShellSecretStart @('--discovery', '--validate-only')
        Assert-True ($result.ExitCode -ne 0) `
            'start-secrets.sh accepted unsupported dotenv syntax.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start-secrets.sh called docker after rejecting unsupported dotenv syntax.'
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_UNSUPPORTED_DOTENV_SENTINEL'))) `
            'start-secrets.sh leaked the value from an unsupported dotenv entry.'
    }
    Set-SafeSecretDotEnv $envFile

    Remove-Item -LiteralPath $envFile -Force
    $result = Invoke-TestShellSecretStart @('--discovery', '--validate-only')
    Assert-True ($result.ExitCode -ne 0) 'start-secrets.sh accepted a missing .env.'
    Assert-True ($result.Calls.Count -eq 0) 'start-secrets.sh called docker without .env.'
    Assert-True (-not (Test-Path -LiteralPath $envFile)) 'start-secrets.sh created a missing .env.'
    Set-SafeSecretDotEnv $envFile

    Remove-Item -LiteralPath (Join-Path $TestRoot 'secrets/tmdb_access_token.txt') -Force
    $result = Invoke-TestShellSecretStart @('--discovery', '--validate-only')
    Assert-True ($result.ExitCode -ne 0) 'start-secrets.sh accepted a missing selected Secret file.'
    Assert-True ($result.Calls.Count -eq 0) 'start-secrets.sh called docker with a missing selected Secret file.'
    Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_FILE_CONTENT_SENTINEL'))) `
        'start-secrets.sh printed Secret contents while reporting a missing file.'
    New-SyntheticSecretFiles $TestRoot @('tmdb_access_token.txt')

    foreach ($arguments in @(
        ,@('--discovery', '--avistaz', '--validate-only'),
        ,@('--discovery', '--qb', '--validate-only')
    )) {
        $result = Invoke-TestShellSecretStart $arguments
        Assert-True ($result.ExitCode -ne 0) `
            'start-secrets.sh accepted a selected layer whose Secret files were missing.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start-secrets.sh called docker with missing optional-layer Secret files.'
    }

    foreach ($name in $BlockedProcessEnvironment) {
        $result = Invoke-TestShellSecretStart `
            -ScriptArguments @('--discovery', '--validate-only') `
            -OverrideName $name `
            -OverrideValue 'NON_SECRET_PROCESS_OVERRIDE_SENTINEL'
        Assert-True ($result.ExitCode -ne 0) "start-secrets.sh accepted process override $name."
        Assert-True ($result.Calls.Count -eq 0) `
            "start-secrets.sh called docker after rejecting process override $name."
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_PROCESS_OVERRIDE_SENTINEL'))) `
            "start-secrets.sh leaked the process override sentinel for $name."
    }
    $result = Invoke-TestShellSecretStart `
        -ScriptArguments @('--discovery', '--validate-only') `
        -OverrideName 'AUTH_LOCAL_USERNAME' `
        -OverrideValue ''
    Assert-True ($result.ExitCode -ne 0) `
        'start-secrets.sh accepted an exported empty Secret plaintext override.'
    Assert-True ($result.Calls.Count -eq 0) `
        'start-secrets.sh called docker after rejecting an empty process override.'
    $result = Invoke-TestShellSecretStart `
        -ScriptArguments @('--discovery', '--validate-only') `
        -OverrideName 'COMPOSE_PROFILES' `
        -OverrideValue ''
    Assert-True ($result.ExitCode -ne 0) 'start-secrets.sh accepted exported empty COMPOSE_PROFILES.'
    Assert-True ($result.Calls.Count -eq 0) `
        'start-secrets.sh called docker after rejecting empty ambient COMPOSE_PROFILES.'

    Set-SafeSecretDotEnv $envFile -Suffix @('COMPOSE_PROFILES=download-execution')
    $result = Invoke-TestShellSecretStart @('--discovery', '--validate-only')
    Assert-True ($result.ExitCode -ne 0) 'start-secrets.sh accepted non-empty COMPOSE_PROFILES from .env.'
    Assert-True ($result.Calls.Count -eq 0) `
        'start-secrets.sh called docker after rejecting .env COMPOSE_PROFILES.'

    Set-SafeSecretDotEnv $envFile -Suffix @('AUTH_LOCAL_PASSWORD=${NON_SECRET_INTERPOLATION_SENTINEL}')
    $result = Invoke-TestShellSecretStart @('--discovery', '--validate-only')
    Assert-True ($result.ExitCode -ne 0) 'start-secrets.sh accepted dotenv variable interpolation.'
    Assert-True ($result.Calls.Count -eq 0) `
        'start-secrets.sh called docker after dotenv interpolation was rejected.'
    Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_INTERPOLATION_SENTINEL'))) `
        'start-secrets.sh leaked a dotenv interpolation sentinel.'

    foreach ($name in $PlaintextSecretEnvironmentNames) {
        Set-SafeSecretDotEnv $envFile -Suffix @("${name}=NON_SECRET_PLAINTEXT_CREDENTIAL_SENTINEL")
        $result = Invoke-TestShellSecretStart @('--discovery', '--validate-only')
        Assert-True ($result.ExitCode -ne 0) `
            "start-secrets.sh accepted plaintext credential $name in .env."
        Assert-True ($result.Calls.Count -eq 0) `
            "start-secrets.sh called docker after rejecting plaintext credential $name."
        Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_PLAINTEXT_CREDENTIAL_SENTINEL'))) `
            "start-secrets.sh leaked plaintext credential $name."
    }
    Set-SafeSecretDotEnv $envFile -Prefix @(
        'AUTH_LOCAL_PASSWORD=NON_SECRET_PLAINTEXT_CREDENTIAL_SENTINEL'
    )
    $result = Invoke-TestShellSecretStart @('--discovery', '--validate-only')
    Assert-True ($result.ExitCode -ne 0) `
        'start-secrets.sh accepted an earlier non-empty duplicate plaintext credential.'
    Assert-True ($result.Calls.Count -eq 0) `
        'start-secrets.sh called docker after an earlier plaintext credential was rejected.'
    Assert-True (-not (($result.Output -join "`n").Contains('NON_SECRET_PLAINTEXT_CREDENTIAL_SENTINEL'))) `
        'start-secrets.sh leaked an earlier duplicate plaintext credential.'

    Set-SafeSecretDotEnv $envFile -Suffix @('POSTGRES_PASSWORD=change-me-before-production')
    $result = Invoke-TestShellSecretStart @('--discovery', '--validate-only')
    Assert-True ($result.ExitCode -ne 0) 'start-secrets.sh accepted the example PostgreSQL password.'
    Assert-True ($result.Calls.Count -eq 0) `
        'start-secrets.sh called docker after rejecting the example PostgreSQL password.'

    Set-SafeSecretDotEnv $envFile
    foreach ($arguments in @(
        ,@('--validate-only'),
        ,@('--discovery', '--profile', 'automation-preflight', '--validate-only'),
        ,@('--discovery', '--profile', 'download-monitor', '--validate-only'),
        ,@('--discovery', '--qb', '--profile', 'download-execution', '--validate-only'),
        ,@('--discovery', '--profile', 'unsupported-profile', '--validate-only')
    )) {
        $result = Invoke-TestShellSecretStart $arguments
        Assert-True ($result.ExitCode -ne 0) `
            'start-secrets.sh accepted a missing layer dependency or unsupported profile.'
        Assert-True ($result.Calls.Count -eq 0) `
            'start-secrets.sh called docker after rejecting a layer/profile selection.'
    }

    New-SyntheticSecretFiles $TestRoot @($AvistaZSecretNames + $QbSecretNames)
    $result = Invoke-TestShellSecretStart @(
        '--profile', 'download-monitor',
        '--qb',
        '--profile', 'download-execution',
        '--discovery',
        '--profile', 'automation-preflight',
        '--avistaz',
        '--validate-only'
    )
    Assert-True ($result.ExitCode -eq 0) 'start-secrets.sh rejected a valid all-layer/profile selection.'
    Assert-SecretDockerCalls `
        $result.Calls `
        $wslRoot `
        $wslEnvFile `
        @($wslComposeFile, $wslDiscoveryCompose, $wslAvistaZCompose, $wslQbCompose) `
        @('automation-preflight', 'download-execution', 'download-monitor') `
        -Label 'start-secrets.sh fixed layer/profile order' `
        -ExpectedDockerContext 'default'

    $result = Invoke-TestShellSecretStart @('--discovery')
    Assert-True ($result.ExitCode -eq 0) 'start-secrets.sh rejected a safe synthetic full startup.'
    Assert-SecretDockerCalls `
        $result.Calls `
        $wslRoot `
        $wslEnvFile `
        @($wslComposeFile, $wslDiscoveryCompose) `
        -FullStart `
        -Label 'start-secrets.sh synthetic full startup' `
        -ExpectedDockerContext 'default'
}

Assert-StaticComposeContract $PowerShellStart 'start.ps1'
Assert-StaticComposeContract $ShellStart 'start.sh'
Assert-StaticComposeContract $PowerShellSecretStart 'start-secrets.ps1'
Assert-StaticComposeContract $ShellSecretStart 'start-secrets.sh'
Assert-SecretFilesExistenceOnlyContract `
    $PowerShellSecretStart `
    'start-secrets.ps1' `
    -PowerShell
Assert-SecretFilesExistenceOnlyContract $ShellSecretStart 'start-secrets.sh'

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("unin-startup-gates-{0}" -f [Guid]::NewGuid().ToString('N'))
try {
    Invoke-PowerShellSecretGateChecks $tempRoot
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
    Invoke-PowerShellGateChecks $tempRoot
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
    Invoke-ShellGateChecks $tempRoot
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
    Invoke-ShellSecretGateChecks $tempRoot
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}

Write-Host "Startup gate verification passed for four launchers and $($BlockedProcessEnvironment.Count) blocked process variables." -ForegroundColor Green
