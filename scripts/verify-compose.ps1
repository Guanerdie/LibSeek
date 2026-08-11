$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot '.env.example'
$ComposeFile = Join-Path $ProjectRoot 'compose.yaml'
$AllProfiles = @(
    'automation-preflight',
    'download-execution',
    'download-monitor'
)

$OverrideFiles = @{
    discovery = Join-Path $ProjectRoot 'deploy\compose.secrets.discovery.yaml.example'
    avistaz = Join-Path $ProjectRoot 'deploy\compose.secrets.avistaz.yaml.example'
    qb = Join-Path $ProjectRoot 'deploy\compose.secrets.qb.yaml.example'
    legacy = Join-Path $ProjectRoot 'deploy\compose.secrets.yaml.example'
}

$AuthSecrets = @(
    'auth_local_username',
    'auth_local_password',
    'auth_session_signing_key'
)
$NextfindTmdbSecrets = @(
    'nextfind_username',
    'nextfind_password',
    'tmdb_access_token'
)
$DiscoverySecrets = $AuthSecrets + $NextfindTmdbSecrets
$AvistazSecrets = @('avistaz_username', 'avistaz_password', 'avistaz_pid')
$QbSecrets = @('qb_base_url', 'qb_username', 'qb_password')

$SecretEnvironment = @{
    auth_local_username = @('AUTH_LOCAL_USERNAME', 'AUTH_LOCAL_USERNAME_FILE')
    auth_local_password = @('AUTH_LOCAL_PASSWORD', 'AUTH_LOCAL_PASSWORD_FILE')
    auth_session_signing_key = @(
        'AUTH_SESSION_SIGNING_KEY',
        'AUTH_SESSION_SIGNING_KEY_FILE'
    )
    nextfind_username = @('NEXTFIND_USERNAME', 'NEXTFIND_USERNAME_FILE')
    nextfind_password = @('NEXTFIND_PASSWORD', 'NEXTFIND_PASSWORD_FILE')
    tmdb_access_token = @('TMDB_ACCESS_TOKEN', 'TMDB_ACCESS_TOKEN_FILE')
    avistaz_username = @('AVISTAZ_USERNAME', 'AVISTAZ_USERNAME_FILE')
    avistaz_password = @('AVISTAZ_PASSWORD', 'AVISTAZ_PASSWORD_FILE')
    avistaz_pid = @('AVISTAZ_PID', 'AVISTAZ_PID_FILE')
    qb_base_url = @('QB_BASE_URL', 'QB_BASE_URL_FILE')
    qb_username = @('QB_USERNAME', 'QB_USERNAME_FILE')
    qb_password = @('QB_PASSWORD', 'QB_PASSWORD_FILE')
}

$DangerousFlags = @(
    'ENABLE_TMDB_LIVE',
    'ENABLE_AVISTAZ_LIVE_SEARCH',
    'ENABLE_QB_READ_ONLY',
    'ENABLE_AUTOMATION_ENGINE',
    'ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE',
    'ENABLE_DOWNLOAD_EXECUTOR',
    'ENABLE_AVISTAZ_TORRENT_FETCH',
    'ENABLE_QB_WRITE',
    'ENABLE_DOWNLOAD_MONITOR',
    'ENABLE_MEDIA_IMPORT_CONTROL_PLANE'
)

$DeterministicEnvironmentKeys = @(
    'MEDIA_IMPORT_TARGET_ROOT_REFS',
    'MEDIA_IMPORT_PREFLIGHT_MAX_AGE_SECONDS',
    'QB_TARGET_SAVE_PATH',
    'QB_ALLOWED_SAVE_PATHS'
)

$RequiredDangerousFlagsByService = @{
    api = @(
        'ENABLE_TMDB_LIVE',
        'ENABLE_AVISTAZ_LIVE_SEARCH',
        'ENABLE_QB_READ_ONLY',
        'ENABLE_AUTOMATION_ENGINE',
        'ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE',
        'ENABLE_DOWNLOAD_EXECUTOR',
        'ENABLE_AVISTAZ_TORRENT_FETCH',
        'ENABLE_QB_WRITE',
        'ENABLE_MEDIA_IMPORT_CONTROL_PLANE'
    )
    worker = @(
        'ENABLE_TMDB_LIVE',
        'ENABLE_AVISTAZ_LIVE_SEARCH',
        'ENABLE_AUTOMATION_ENGINE',
        'ENABLE_AVISTAZ_TORRENT_FETCH'
    )
    'automation-preflight' = @(
        'ENABLE_AUTOMATION_ENGINE',
        'ENABLE_QB_READ_ONLY',
        'ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE',
        'ENABLE_DOWNLOAD_EXECUTOR',
        'ENABLE_AVISTAZ_LIVE_SEARCH',
        'ENABLE_AVISTAZ_TORRENT_FETCH',
        'ENABLE_QB_WRITE'
    )
    'download-executor' = @(
        'ENABLE_AUTOMATION_ENGINE',
        'ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE',
        'ENABLE_DOWNLOAD_EXECUTOR',
        'ENABLE_AVISTAZ_LIVE_SEARCH',
        'ENABLE_AVISTAZ_TORRENT_FETCH',
        'ENABLE_QB_WRITE'
    )
    'download-monitor' = @(
        'ENABLE_DOWNLOAD_MONITOR',
        'ENABLE_QB_READ_ONLY'
    )
}

function Get-ComposeConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [string[]]$ComposeOverrides = @(),
        [string[]]$Profiles = @()
    )

    $composeArgs = @(
        'compose',
        '--project-directory', $ProjectRoot,
        '--env-file', $EnvFile,
        '-f', $ComposeFile
    )
    foreach ($composeOverride in $ComposeOverrides) {
        $composeArgs += @('-f', $composeOverride)
    }
    foreach ($profile in $Profiles) {
        $composeArgs += @('--profile', $profile)
    }
    $composeArgs += @('config', '--format', 'json')

    $jsonLines = & docker @composeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose config failed for rendering '$Label'"
    }
    try {
        return (($jsonLines -join [Environment]::NewLine) | ConvertFrom-Json)
    }
    catch {
        throw "docker compose returned invalid JSON for rendering '$Label': $($_.Exception.Message)"
    }
}

function Get-Service {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Config,
        [Parameter(Mandatory = $true)]
        [string]$ServiceName,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $property = $Config.services.PSObject.Properties[$ServiceName]
    if ($null -eq $property) {
        throw "Rendering '$Label' is missing Compose service '$ServiceName'"
    }
    return $property.Value
}

function Assert-ServiceMissing {
    param(
        [object]$Config,
        [string]$ServiceName,
        [string]$Label
    )

    if ($null -ne $Config.services.PSObject.Properties[$ServiceName]) {
        throw "Rendering '$Label' unexpectedly includes profile service '$ServiceName'"
    }
}

function Assert-EnvironmentValue {
    param(
        [object]$Config,
        [string]$ServiceName,
        [string]$Key,
        [string]$Expected,
        [string]$Label
    )

    $service = Get-Service $Config $ServiceName $Label
    $property = $service.environment.PSObject.Properties[$Key]
    if ($null -eq $property) {
        throw "Rendering '$Label' service '$ServiceName' is missing environment key '$Key'"
    }
    if ([string]$property.Value -cne $Expected) {
        throw "Rendering '$Label' service '$ServiceName' has an invalid value for '$Key'"
    }
}

function Assert-ExactStringSet {
    param(
        [string[]]$Actual,
        [string[]]$Expected,
        [string]$Description
    )

    $actualSorted = @($Actual | Sort-Object -Unique)
    $expectedSorted = @($Expected | Sort-Object -Unique)
    if ($actualSorted.Count -ne $expectedSorted.Count) {
        throw "$Description count mismatch: expected $($expectedSorted.Count), got $($actualSorted.Count)"
    }
    for ($index = 0; $index -lt $expectedSorted.Count; $index += 1) {
        if ($actualSorted[$index] -cne $expectedSorted[$index]) {
            throw "$Description mismatch"
        }
    }
}

function Get-ExpectedSecretsForService {
    param(
        [string]$ServiceName,
        [string[]]$Layers
    )

    $expected = @()
    if ($Layers -contains 'discovery') {
        if ($ServiceName -eq 'api') {
            $expected += $DiscoverySecrets
        }
        elseif ($ServiceName -eq 'worker') {
            $expected += $NextfindTmdbSecrets
        }
    }
    if (
        $Layers -contains 'avistaz' -and
        $ServiceName -in @('api', 'worker', 'download-executor')
    ) {
        $expected += $AvistazSecrets
    }
    if (
        $Layers -contains 'qb' -and
        $ServiceName -in @(
            'api',
            'automation-preflight',
            'download-executor',
            'download-monitor'
        )
    ) {
        $expected += $QbSecrets
    }
    return @($expected | Sort-Object -Unique)
}

function Get-ExpectedTopLevelSecrets {
    param([string[]]$Layers)

    $expected = @()
    if ($Layers -contains 'discovery') {
        $expected += $DiscoverySecrets
    }
    if ($Layers -contains 'avistaz') {
        $expected += $AvistazSecrets
    }
    if ($Layers -contains 'qb') {
        $expected += $QbSecrets
    }
    return @($expected | Sort-Object -Unique)
}

function Assert-DangerousFlagsDisabled {
    param(
        [object]$Config,
        [string]$Label
    )

    foreach ($serviceProperty in $Config.services.PSObject.Properties) {
        $environment = $serviceProperty.Value.environment
        if ($null -eq $environment) {
            continue
        }
        foreach ($flag in $DangerousFlags) {
            $property = $environment.PSObject.Properties[$flag]
            if ($null -ne $property -and [string]$property.Value -cne 'false') {
                throw "Rendering '$Label' service '$($serviceProperty.Name)' enables '$flag'"
            }
        }
    }

    foreach ($serviceName in $RequiredDangerousFlagsByService.Keys) {
        if ($null -eq $Config.services.PSObject.Properties[$serviceName]) {
            continue
        }
        foreach ($flag in $RequiredDangerousFlagsByService[$serviceName]) {
            Assert-EnvironmentValue $Config $serviceName $flag 'false' $Label
        }
    }
}

function Assert-SecretContract {
    param(
        [object]$Config,
        [string[]]$Layers,
        [string]$Label
    )

    $expectedTopLevel = @(Get-ExpectedTopLevelSecrets $Layers)
    $actualTopLevel = @()
    if ($null -ne $Config.secrets) {
        $actualTopLevel = @($Config.secrets.PSObject.Properties.Name)
    }
    Assert-ExactStringSet $actualTopLevel $expectedTopLevel (
        "Rendering '$Label' top-level Secret sources"
    )

    foreach ($secretName in $expectedTopLevel) {
        $secretProperty = $Config.secrets.PSObject.Properties[$secretName]
        if ($null -eq $secretProperty) {
            throw "Rendering '$Label' is missing top-level Secret '$secretName'"
        }
        $expectedFile = [IO.Path]::GetFullPath(
            (Join-Path $ProjectRoot "secrets\$secretName.txt")
        )
        $actualFile = [IO.Path]::GetFullPath([string]$secretProperty.Value.file)
        if ($actualFile -cne $expectedFile) {
            throw "Rendering '$Label' Secret '$secretName' uses an invalid source path"
        }
    }

    foreach ($serviceProperty in $Config.services.PSObject.Properties) {
        $serviceName = $serviceProperty.Name
        $service = $serviceProperty.Value
        $expected = @(Get-ExpectedSecretsForService $serviceName $Layers)
        $secretEntries = @(
            $service.secrets |
                Where-Object { $null -ne $_ }
        )
        $actual = @($secretEntries | ForEach-Object { $_.source })
        Assert-ExactStringSet $actual $expected (
            "Rendering '$Label' service '$serviceName' Secret permissions"
        )

        foreach ($entry in $secretEntries) {
            $expectedTarget = "/run/secrets/$($entry.source)"
            if ([string]$entry.target -cne $expectedTarget) {
                throw "Rendering '$Label' service '$serviceName' has an invalid target for Secret '$($entry.source)'"
            }
        }

        $environment = $service.environment
        if ($null -eq $environment) {
            if ($expected.Count -ne 0) {
                throw "Rendering '$Label' service '$serviceName' is missing its Secret environment bindings"
            }
            continue
        }
        foreach ($secretName in $SecretEnvironment.Keys) {
            $plainKey = $SecretEnvironment[$secretName][0]
            $fileKey = $SecretEnvironment[$secretName][1]
            $plainProperty = $environment.PSObject.Properties[$plainKey]
            $fileProperty = $environment.PSObject.Properties[$fileKey]
            if ($expected -contains $secretName) {
                if ($null -ne $plainProperty -and [string]$plainProperty.Value -cne '') {
                    throw "Rendering '$Label' service '$serviceName' retains plaintext '$plainKey'"
                }
                if (
                    $null -eq $fileProperty -or
                    [string]$fileProperty.Value -cne "/run/secrets/$secretName"
                ) {
                    throw "Rendering '$Label' service '$serviceName' has an invalid '$fileKey'"
                }
            }
            elseif (
                $null -ne $fileProperty -and
                [string]$fileProperty.Value -eq "/run/secrets/$secretName"
            ) {
                throw "Rendering '$Label' service '$serviceName' points to unmounted Secret '$secretName'"
            }
        }
    }
}

$RenderingDefinitions = @(
    [pscustomobject]@{
        Label = 'base-default'
        ComposeOverrides = @()
        Profiles = @()
        Layers = @()
    },
    [pscustomobject]@{
        Label = 'base-all-profiles'
        ComposeOverrides = @()
        Profiles = $AllProfiles
        Layers = @()
    },
    [pscustomobject]@{
        Label = 'discovery-default'
        ComposeOverrides = @($OverrideFiles.discovery)
        Profiles = @()
        Layers = @('discovery')
    },
    [pscustomobject]@{
        Label = 'discovery-all-profiles'
        ComposeOverrides = @($OverrideFiles.discovery)
        Profiles = $AllProfiles
        Layers = @('discovery')
    },
    [pscustomobject]@{
        Label = 'avistaz-all-profiles'
        ComposeOverrides = @($OverrideFiles.avistaz)
        Profiles = $AllProfiles
        Layers = @('avistaz')
    },
    [pscustomobject]@{
        Label = 'qb-all-profiles'
        ComposeOverrides = @($OverrideFiles.qb)
        Profiles = $AllProfiles
        Layers = @('qb')
    },
    [pscustomobject]@{
        Label = 'discovery-avistaz-all-profiles'
        ComposeOverrides = @(
            $OverrideFiles.discovery,
            $OverrideFiles.avistaz
        )
        Profiles = $AllProfiles
        Layers = @('discovery', 'avistaz')
    },
    [pscustomobject]@{
        Label = 'discovery-qb-all-profiles'
        ComposeOverrides = @(
            $OverrideFiles.discovery,
            $OverrideFiles.qb
        )
        Profiles = $AllProfiles
        Layers = @('discovery', 'qb')
    },
    [pscustomobject]@{
        Label = 'avistaz-qb-all-profiles'
        ComposeOverrides = @(
            $OverrideFiles.avistaz,
            $OverrideFiles.qb
        )
        Profiles = $AllProfiles
        Layers = @('avistaz', 'qb')
    },
    [pscustomobject]@{
        Label = 'all-layered-all-profiles'
        ComposeOverrides = @(
            $OverrideFiles.discovery,
            $OverrideFiles.avistaz,
            $OverrideFiles.qb
        )
        Profiles = $AllProfiles
        Layers = @('discovery', 'avistaz', 'qb')
    },
    [pscustomobject]@{
        Label = 'legacy-full-default'
        ComposeOverrides = @($OverrideFiles.legacy)
        Profiles = @()
        Layers = @('discovery', 'avistaz', 'qb')
    },
    [pscustomobject]@{
        Label = 'legacy-full-all-profiles'
        ComposeOverrides = @($OverrideFiles.legacy)
        Profiles = $AllProfiles
        Layers = @('discovery', 'avistaz', 'qb')
    }
)

$EnvironmentOverrides = @{}
$variablesToRestore = @(
    $SecretEnvironment.Values |
        ForEach-Object { $_ } |
        Sort-Object -Unique
) + $DangerousFlags + $DeterministicEnvironmentKeys + @('COMPOSE_PROFILES')

foreach ($variableName in $variablesToRestore) {
    $EnvironmentOverrides[$variableName] = [Environment]::GetEnvironmentVariable(
        $variableName,
        'Process'
    )
}

try {
    foreach ($secretMetadata in $SecretEnvironment.Values) {
        [Environment]::SetEnvironmentVariable(
            $secretMetadata[0],
            'NON_SECRET_COMPOSE_VERIFY_SENTINEL',
            'Process'
        )
        [Environment]::SetEnvironmentVariable(
            $secretMetadata[1],
            'NON_SECRET_FILE_VERIFY_SENTINEL',
            'Process'
        )
    }
    foreach ($flag in $DangerousFlags) {
        [Environment]::SetEnvironmentVariable($flag, $null, 'Process')
    }
    foreach ($key in $DeterministicEnvironmentKeys) {
        [Environment]::SetEnvironmentVariable($key, $null, 'Process')
    }
    [Environment]::SetEnvironmentVariable('COMPOSE_PROFILES', $null, 'Process')

    $renderings = @{}
    foreach ($definition in $RenderingDefinitions) {
        $config = Get-ComposeConfig `
            -Label $definition.Label `
            -ComposeOverrides $definition.ComposeOverrides `
            -Profiles $definition.Profiles
        $renderings[$definition.Label] = $config
        Assert-DangerousFlagsDisabled $config $definition.Label
        Assert-SecretContract $config $definition.Layers $definition.Label
        Assert-EnvironmentValue `
            $config `
            'api' `
            'MEDIA_IMPORT_TARGET_ROOT_REFS' `
            '' `
            $definition.Label
        Assert-EnvironmentValue `
            $config `
            'api' `
            'MEDIA_IMPORT_PREFLIGHT_MAX_AGE_SECONDS' `
            '300' `
            $definition.Label
    }

    foreach ($label in @(
        'base-default',
        'discovery-default',
        'legacy-full-default'
    )) {
        foreach ($serviceName in @(
            'automation-preflight',
            'download-executor',
            'download-monitor'
        )) {
            Assert-ServiceMissing $renderings[$label] $serviceName $label
        }
    }

    foreach ($serviceName in @(
        'automation-preflight',
        'download-executor',
        'download-monitor'
    )) {
        $null = Get-Service `
            $renderings['base-all-profiles'] `
            $serviceName `
            'base-all-profiles'
    }
    Assert-EnvironmentValue `
        $renderings['base-all-profiles'] `
        'download-monitor' `
        'QB_TARGET_SAVE_PATH' `
        '' `
        'base-all-profiles'
    Assert-EnvironmentValue `
        $renderings['base-all-profiles'] `
        'download-monitor' `
        'QB_ALLOWED_SAVE_PATHS' `
        '' `
        'base-all-profiles'
}
finally {
    foreach ($variableName in $variablesToRestore) {
        [Environment]::SetEnvironmentVariable(
            $variableName,
            $EnvironmentOverrides[$variableName],
            'Process'
        )
    }
}

Write-Host (
    "Compose contract verification passed ($($RenderingDefinitions.Count) renderings: layered + legacy)."
) -ForegroundColor Green
