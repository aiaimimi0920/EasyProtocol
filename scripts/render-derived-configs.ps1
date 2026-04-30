param(
    [string]$ConfigPath = 'config.yaml',
    [switch]$ServiceBase,
    [switch]$EasyProtocol,
    [string]$ServiceOutput = 'deploy/service/base/config/config.yaml',
    [string]$ServiceEnvOutput = 'deploy/service/base/config/runtime.env',
    [string]$StackConfigOutput = 'deploy/stacks/easy-protocol/generated/easy-protocol.config.yaml',
    [string]$StackEnvOutput = 'deploy/stacks/easy-protocol/generated/stack.env'
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
$args = @($renderer, '--root-config', $resolvedConfigPath)
if ($ServiceBase) {
    $args += @('--service-output', (Join-Path (Get-EasyProtocolRepoRoot) $ServiceOutput))
    $args += @('--service-env-output', (Join-Path (Get-EasyProtocolRepoRoot) $ServiceEnvOutput))
}
if ($EasyProtocol) {
    $args += @('--stack-config-output', (Join-Path (Get-EasyProtocolRepoRoot) $StackConfigOutput))
    $args += @('--stack-env-output', (Join-Path (Get-EasyProtocolRepoRoot) $StackEnvOutput))
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
