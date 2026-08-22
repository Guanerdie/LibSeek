[CmdletBinding()]
param(
    [switch]$Build
)

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$arguments = @('compose', '-f', (Join-Path $projectRoot 'compose.yaml'), 'up', '-d')
if ($Build) {
    $arguments += '--build'
}

& docker @arguments
exit $LASTEXITCODE
