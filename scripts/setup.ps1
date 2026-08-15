[CmdletBinding()]
param(
    [switch]$ReplaceExisting,
    [switch]$ValidatePathsOnly,
    [switch]$RecoverStagingOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WizardPath = Join-Path $ProjectRoot 'backend/app/tools/local_setup_wizard.py'
$SecretsDirectory = Join-Path $ProjectRoot 'secrets'
$StagingDirectory = Join-Path $SecretsDirectory '.setup-staging'
$EnvFile = Join-Path $ProjectRoot '.env'
$LockFile = Join-Path $StagingDirectory 'setup.lock'
$AclMarkerFile = Join-Path $StagingDirectory 'setup.acl-marker'
$AclNonceEnvironmentName = 'UNIN_SETUP_ACL_NONCE'
$CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$SystemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$FixedSecretNames = @(
    'auth_local_username.txt',
    'auth_local_password.txt',
    'auth_session_signing_key.txt',
    'nextfind_username.txt',
    'nextfind_password.txt',
    'tmdb_access_token.txt',
    'avistaz_username.txt',
    'avistaz_password.txt',
    'avistaz_pid.txt',
    'qb_base_url.txt',
    'qb_username.txt',
    'qb_password.txt'
)

function Assert-ProjectPathAncestorsSafe {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $rootFullPath = [IO.Path]::GetFullPath($ProjectRoot)
    $candidateFullPath = [IO.Path]::GetFullPath($LiteralPath)
    $comparison = [StringComparison]::OrdinalIgnoreCase
    $rootPrefix = $rootFullPath + [IO.Path]::DirectorySeparatorChar
    if (
        -not $candidateFullPath.Equals($rootFullPath, $comparison) -and
        -not $candidateFullPath.StartsWith($rootPrefix, $comparison)
    ) {
        throw "Setup path escapes the project root: $LiteralPath"
    }

    $rootItem = Get-Item -LiteralPath $rootFullPath -Force -ErrorAction Stop
    if (
        ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not ($rootItem -is [IO.DirectoryInfo])
    ) {
        throw "The project root must be a regular directory: $rootFullPath"
    }

    $currentPath = [IO.Path]::GetDirectoryName($candidateFullPath)
    while (
        $null -ne $currentPath -and
        (
            $currentPath.Equals($rootFullPath, $comparison) -or
            $currentPath.StartsWith($rootPrefix, $comparison)
        )
    ) {
        try {
            $currentItem = Get-Item `
                -LiteralPath $currentPath `
                -Force `
                -ErrorAction Stop
        }
        catch [System.Management.Automation.ItemNotFoundException] {
            $currentItem = $null
        }
        if (
            $null -ne $currentItem -and
            (
                ($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
                -not ($currentItem -is [IO.DirectoryInfo])
            )
        ) {
            throw "A setup path ancestor is not a regular directory: $currentPath"
        }
        if ($currentPath.Equals($rootFullPath, $comparison)) {
            break
        }
        $currentPath = [IO.Path]::GetDirectoryName($currentPath)
    }
}

function Assert-SingleHardLink {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $fsutil = Get-Command fsutil.exe -ErrorAction Stop
    $linkLines = @(& $fsutil.Source hardlink list $LiteralPath 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to verify the hard-link count for setup file: $LiteralPath"
    }
    $links = @(
        $linkLines | Where-Object {
            $_ -is [string] -and -not [string]::IsNullOrWhiteSpace($_)
        }
    )
    if ($links.Count -ne 1) {
        throw "Setup files must have exactly one hard link: $LiteralPath"
    }
}

function Get-ValidatedPathItem {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)]
        [ValidateSet('Directory', 'File')]
        [string]$ExpectedType,
        [switch]$AllowMissing,
        [switch]$SkipHardLinkCheck
    )

    Assert-ProjectPathAncestorsSafe -LiteralPath $LiteralPath
    try {
        $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    }
    catch [System.Management.Automation.ItemNotFoundException] {
        if ($AllowMissing) {
            return $null
        }
        throw
    }

    if (
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne
        0
    ) {
        throw "Reparse points are not allowed for setup paths: $LiteralPath"
    }
    if ($ExpectedType -eq 'Directory' -and -not ($item -is [IO.DirectoryInfo])) {
        throw "Expected a directory for setup path: $LiteralPath"
    }
    if ($ExpectedType -eq 'File' -and -not ($item -is [IO.FileInfo])) {
        throw "Expected a regular file for setup path: $LiteralPath"
    }
    if ($ExpectedType -eq 'File' -and -not $SkipHardLinkCheck) {
        Assert-SingleHardLink -LiteralPath $LiteralPath
    }
    return $item
}

function Assert-ExistingSetupPathsSafe {
    param([switch]$SkipHardLinkCheck)

    Get-ValidatedPathItem `
        -LiteralPath $SecretsDirectory `
        -ExpectedType Directory `
        -AllowMissing | Out-Null
    Get-ValidatedPathItem `
        -LiteralPath $StagingDirectory `
        -ExpectedType Directory `
        -AllowMissing | Out-Null
    Get-ValidatedPathItem `
        -LiteralPath $EnvFile `
        -ExpectedType File `
        -SkipHardLinkCheck:$SkipHardLinkCheck `
        -AllowMissing | Out-Null
    foreach ($name in $FixedSecretNames) {
        Get-ValidatedPathItem `
            -LiteralPath (Join-Path $SecretsDirectory $name) `
            -ExpectedType File `
            -SkipHardLinkCheck:$SkipHardLinkCheck `
            -AllowMissing | Out-Null
    }
    Get-ValidatedPathItem `
        -LiteralPath $LockFile `
        -ExpectedType File `
        -SkipHardLinkCheck:$SkipHardLinkCheck `
        -AllowMissing | Out-Null
    Get-ValidatedPathItem `
        -LiteralPath $AclMarkerFile `
        -ExpectedType File `
        -SkipHardLinkCheck:$SkipHardLinkCheck `
        -AllowMissing | Out-Null
}

function New-SafeDirectory {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $existing = Get-ValidatedPathItem `
        -LiteralPath $LiteralPath `
        -ExpectedType Directory `
        -AllowMissing
    if ($null -eq $existing) {
        [void][IO.Directory]::CreateDirectory($LiteralPath)
    }
    Get-ValidatedPathItem `
        -LiteralPath $LiteralPath `
        -ExpectedType Directory | Out-Null
}

function Set-RestrictedAcl {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)]
        [ValidateSet('Directory', 'File')]
        [string]$ExpectedType
    )

    Get-ValidatedPathItem `
        -LiteralPath $LiteralPath `
        -ExpectedType $ExpectedType | Out-Null
    $acl = if ($ExpectedType -eq 'Directory') {
        [Security.AccessControl.DirectorySecurity]::new()
    }
    else {
        [Security.AccessControl.FileSecurity]::new()
    }
    $acl.SetAccessRuleProtection($true, $false)

    $inheritance = if ($ExpectedType -eq 'Directory') {
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit
    }
    else {
        [Security.AccessControl.InheritanceFlags]::None
    }
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $accessType = [Security.AccessControl.AccessControlType]::Allow
    foreach ($sid in @($CurrentSid, $SystemSid) | Select-Object -Unique) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            $propagation,
            $accessType
        )
        [void]$acl.AddAccessRule($rule)
    }

    Get-ValidatedPathItem `
        -LiteralPath $LiteralPath `
        -ExpectedType $ExpectedType | Out-Null
    if ($ExpectedType -eq 'Directory') {
        [IO.Directory]::SetAccessControl($LiteralPath, $acl)
    }
    else {
        [IO.File]::SetAccessControl($LiteralPath, $acl)
    }
    Get-ValidatedPathItem `
        -LiteralPath $LiteralPath `
        -ExpectedType $ExpectedType | Out-Null

    $verified = Get-Acl -LiteralPath $LiteralPath
    if (-not $verified.AreAccessRulesProtected) {
        throw "ACL inheritance remains enabled: $LiteralPath"
    }
    $allowedSidValues = @($CurrentSid.Value, $SystemSid.Value)
    $currentUserAllowed = $false
    foreach ($rule in @($verified.Access)) {
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
            throw "Unexpected deny ACL remains on protected path: $LiteralPath"
        }
        try {
            $ruleSid = $rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        }
        catch {
            throw "Unable to verify an ACL identity: $LiteralPath"
        }
        if ($allowedSidValues -notcontains $ruleSid) {
            throw "Unexpected allow ACL remains on protected path: $LiteralPath"
        }
        if (
            ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
            [Security.AccessControl.FileSystemRights]::FullControl
        ) {
            throw "Protected ACL is weaker than FullControl: $LiteralPath"
        }
        if ($ruleSid -eq $CurrentSid.Value) {
            $currentUserAllowed = $true
        }
    }
    if (-not $currentUserAllowed) {
        throw "Current SID does not retain access to protected path: $LiteralPath"
    }
}

function Protect-ExistingConfigurationFiles {
    $envItem = Get-ValidatedPathItem `
        -LiteralPath $EnvFile `
        -ExpectedType File `
        -AllowMissing
    if ($null -ne $envItem) {
        Set-RestrictedAcl -LiteralPath $EnvFile -ExpectedType File
    }
    foreach ($name in $FixedSecretNames) {
        $path = Join-Path $SecretsDirectory $name
        $item = Get-ValidatedPathItem `
            -LiteralPath $path `
            -ExpectedType File `
            -AllowMissing
        if ($null -ne $item) {
            Set-RestrictedAcl -LiteralPath $path -ExpectedType File
        }
    }
    $lockItem = Get-ValidatedPathItem `
        -LiteralPath $LockFile `
        -ExpectedType File `
        -AllowMissing
    if ($null -ne $lockItem) {
        Set-RestrictedAcl -LiteralPath $LockFile -ExpectedType File
    }
}

function Test-Python312 {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Prefix = @()
    )

    try {
        & $Command @Prefix -I -c `
            'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' `
            *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function New-OneTimeAclMarker {
    $existing = Get-ValidatedPathItem `
        -LiteralPath $AclMarkerFile `
        -ExpectedType File `
        -AllowMissing
    if ($null -ne $existing) {
        Remove-Item -LiteralPath $AclMarkerFile -Force
    }

    $randomBytes = [byte[]]::new(32)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($randomBytes)
    }
    finally {
        $generator.Dispose()
    }
    $nonce = [Convert]::ToBase64String($randomBytes)
    $nonceBytes = [Text.Encoding]::ASCII.GetBytes($nonce)
    $stream = [IO.FileStream]::new(
        $AclMarkerFile,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    try {
        $stream.Write($nonceBytes, 0, $nonceBytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
    Get-ValidatedPathItem `
        -LiteralPath $AclMarkerFile `
        -ExpectedType File | Out-Null
    Set-RestrictedAcl -LiteralPath $AclMarkerFile -ExpectedType File
    return $nonce
}

if ($ValidatePathsOnly) {
    Assert-ExistingSetupPathsSafe
    return
}

Assert-ExistingSetupPathsSafe -SkipHardLinkCheck

if ($null -eq $CurrentSid) {
    throw 'Unable to resolve the current Windows user SID.'
}
Get-ValidatedPathItem -LiteralPath $WizardPath -ExpectedType File | Out-Null

$pythonCandidates = @()
$venvPython = Join-Path $ProjectRoot 'backend/.venv/Scripts/python.exe'
$venvItem = Get-ValidatedPathItem `
    -LiteralPath $venvPython `
    -ExpectedType File `
    -AllowMissing
if ($null -ne $venvItem) {
    $pythonCandidates += [PSCustomObject]@{
        Command = $venvPython
        Prefix = @()
    }
}
$pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($null -ne $pythonLauncher) {
    $pythonCandidates += [PSCustomObject]@{
        Command = $pythonLauncher.Source
        Prefix = @('-3.12')
    }
}
$pythonExecutable = Get-Command python.exe -ErrorAction SilentlyContinue
if ($null -ne $pythonExecutable) {
    $pythonCandidates += [PSCustomObject]@{
        Command = $pythonExecutable.Source
        Prefix = @()
    }
}

$pythonCommand = $null
$pythonPrefix = @()
foreach ($candidate in $pythonCandidates) {
    if (Test-Python312 -Command $candidate.Command -Prefix $candidate.Prefix) {
        $pythonCommand = $candidate.Command
        $pythonPrefix = $candidate.Prefix
        break
    }
}
if ($null -eq $pythonCommand) {
    throw 'Python 3.12 or newer is required. The setup launcher will not install a runtime.'
}

$wizardArguments = @($pythonPrefix) + @(
    $WizardPath,
    '--project-root',
    $ProjectRoot
)
if ($ReplaceExisting) {
    $wizardArguments += '--replace-existing'
}

$recoveryExitCode = $null
$recoveryArguments = $wizardArguments + '--recover-only'
& $pythonCommand @recoveryArguments
$recoveryExitCode = $LASTEXITCODE
if ($null -eq $recoveryExitCode -or $recoveryExitCode -ne 0) {
    throw "Local setup staging recovery exited with code $recoveryExitCode"
}

# Recovery removes only strictly identified transaction artifacts. The normal
# single-hard-link gate remains mandatory before any configuration-file ACL.
Assert-ExistingSetupPathsSafe
Set-RestrictedAcl -LiteralPath $SecretsDirectory -ExpectedType Directory
Set-RestrictedAcl -LiteralPath $StagingDirectory -ExpectedType Directory
Protect-ExistingConfigurationFiles
if ($RecoverStagingOnly) {
    return
}

$previousAclNonce = [Environment]::GetEnvironmentVariable(
    $AclNonceEnvironmentName,
    'Process'
)
$wizardExitCode = $null
$aclNonce = $null
try {
    Assert-ExistingSetupPathsSafe
    $aclNonce = New-OneTimeAclMarker
    [Environment]::SetEnvironmentVariable(
        $AclNonceEnvironmentName,
        $aclNonce,
        'Process'
    )
    & $pythonCommand @wizardArguments
    $wizardExitCode = $LASTEXITCODE
}
finally {
    [Environment]::SetEnvironmentVariable(
        $AclNonceEnvironmentName,
        $previousAclNonce,
        'Process'
    )
    Assert-ExistingSetupPathsSafe
    $markerItem = Get-ValidatedPathItem `
        -LiteralPath $AclMarkerFile `
        -ExpectedType File `
        -AllowMissing
    if ($null -ne $markerItem) {
        Remove-Item -LiteralPath $AclMarkerFile -Force
    }
    Set-RestrictedAcl -LiteralPath $SecretsDirectory -ExpectedType Directory
    Set-RestrictedAcl -LiteralPath $StagingDirectory -ExpectedType Directory
    Protect-ExistingConfigurationFiles
}

if ($null -eq $wizardExitCode -or $wizardExitCode -ne 0) {
    throw "Local setup wizard exited with code $wizardExitCode"
}
