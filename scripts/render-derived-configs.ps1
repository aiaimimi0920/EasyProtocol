param(
    [string]$ConfigPath = 'config.yaml',
    [switch]$ServiceBase,
    [switch]$EasyProtocol,
    [string]$ServiceOutput = 'deploy/service/base/config/config.yaml',
    [string]$ServiceEnvOutput = 'deploy/service/base/config/runtime.env',
    [string]$StackConfigOutput = 'deploy/stacks/easy-protocol/generated/easy-protocol.config.yaml',
    [string]$StackEnvOutput = 'deploy/stacks/easy-protocol/generated/stack.env',
    [string]$RegisterOutputDirHost = '',
    [string]$RegisterTeamAuthDirHost = '',
    [string]$RegisterTeamLocalDirHost = '',
    [string]$PythonProviderImage = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')

if (-not $ServiceBase -and -not $EasyProtocol) {
    $ServiceBase = $true
    $EasyProtocol = $true
}

$renderer = Join-Path $PSScriptRoot 'render-derived-configs.py'
if (-not (Test-Path -LiteralPath $renderer)) {
    throw "Missing renderer script: $renderer"
}

Assert-EasyProtocolPythonModule -ModuleName 'yaml' -PackageName 'pyyaml'

$resolvedConfigPath = Resolve-EasyProtocolPath -Path $ConfigPath
$resolveOutputPath = {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }

    return (Join-Path (Get-EasyProtocolRepoRoot) $Path)
}
$args = @($renderer, '--root-config', $resolvedConfigPath)
if (-not [string]::IsNullOrWhiteSpace($RegisterOutputDirHost)) {
    $args += @('--register-output-dir-host', $RegisterOutputDirHost)
}
if (-not [string]::IsNullOrWhiteSpace($RegisterTeamAuthDirHost)) {
    $args += @('--register-team-auth-dir-host', $RegisterTeamAuthDirHost)
}
if (-not [string]::IsNullOrWhiteSpace($RegisterTeamLocalDirHost)) {
    $args += @('--register-team-local-dir-host', $RegisterTeamLocalDirHost)
}
if (-not [string]::IsNullOrWhiteSpace($PythonProviderImage)) {
    $args += @('--python-provider-image', $PythonProviderImage)
}
if ($ServiceBase) {
    $args += @('--service-output', (& $resolveOutputPath -Path $ServiceOutput))
    $args += @('--service-env-output', (& $resolveOutputPath -Path $ServiceEnvOutput))
}
if ($EasyProtocol) {
    $args += @('--stack-config-output', (& $resolveOutputPath -Path $StackConfigOutput))
    $args += @('--stack-env-output', (& $resolveOutputPath -Path $StackEnvOutput))
}

& python @args
if ($LASTEXITCODE -ne 0) {
    throw "Failed to render derived configs with exit code $LASTEXITCODE"
}

if ($ServiceBase) {
    Write-Host "Service config rendered: $ServiceOutput"
    Write-Host "Service env rendered: $ServiceEnvOutput"
}
if ($EasyProtocol) {
    Write-Host "EasyProtocol stack config rendered: $StackConfigOutput"
    Write-Host "EasyProtocol stack env rendered: $StackEnvOutput"
}
