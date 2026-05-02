param(
    [string]$ConfigPath = 'config.yaml',
    [switch]$NoBuild,
    [switch]$SkipRender,
    [switch]$FromGhcr,
    [string]$Image = '',
    [string]$ReleaseTag = '',
    [string]$GhcrOwner = '',
    [string]$ServiceOutput = 'deploy/service/base/config/config.yaml',
    [string]$ServiceEnvOutput = 'deploy/service/base/config/runtime.env',
    [switch]$SkipPull
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot 'lib/easyprotocol-common.ps1')
. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')
. (Join-Path $PSScriptRoot 'lib/easyprotocol-network.ps1')
. (Join-Path $PSScriptRoot 'lib/easyprotocol-ghcr.ps1')

Assert-EasyProtocolCommand -Name 'docker' -Hint 'Install Docker Desktop or another Docker engine first.'
$config = Read-EasyProtocolConfig -ConfigPath $ConfigPath
$stack = if ($config.stack) { $config.stack.easyProtocol } else { $null }
$publishing = $config.publishing
$ghcr = if ($null -ne $publishing) { $publishing.ghcr } else { $null }
$configuredImage = if ($config.serviceBase -and $config.serviceBase.image) { [string]$config.serviceBase.image } else { 'easyprotocol/easy-protocol-service:local' }
$configuredName = [string]($configuredImage -replace '^.+/', '' -replace ':.+$', '')
$serviceImageName = if ([string]::IsNullOrWhiteSpace($configuredName)) { 'easy-protocol-service' } else { $configuredName }
$registry = if ($ghcr -and $ghcr.registry) { [string]$ghcr.registry } else { 'ghcr.io' }
$networkName = if ($stack -and $stack.networkName) { [string]$stack.networkName } else { 'EasyAiMi' }
$composeFile = Join-Path $repoRoot 'deploy/service/base/docker-compose.yaml'
$renderedConfigPath = if ([System.IO.Path]::IsPathRooted($ServiceOutput)) { $ServiceOutput } else { Join-Path $repoRoot $ServiceOutput }
$renderedRuntimeEnvPath = if ([System.IO.Path]::IsPathRooted($ServiceEnvOutput)) { $ServiceEnvOutput } else { Join-Path $repoRoot $ServiceEnvOutput }
$useGhcrDeploy = $FromGhcr -or -not [string]::IsNullOrWhiteSpace($Image) -or -not [string]::IsNullOrWhiteSpace($ReleaseTag)

if (-not $SkipRender) {
    Write-Host 'Rendering service/base config...' -ForegroundColor Cyan
    Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $repoRoot 'scripts/render-derived-configs.ps1') -Arguments @(
        '-ConfigPath', $ConfigPath,
        '-ServiceBase',
        '-ServiceOutput', $ServiceOutput,
        '-ServiceEnvOutput', $ServiceEnvOutput
    ) -FailureMessage 'render-derived-configs.ps1 failed'
}

if (-not (Test-Path -LiteralPath $renderedConfigPath)) {
    throw "Missing rendered service config: $renderedConfigPath"
}
if (-not (Test-Path -LiteralPath $renderedRuntimeEnvPath)) {
    throw "Missing rendered runtime env: $renderedRuntimeEnvPath"
}

if ($useGhcrDeploy) {
    if ([string]::IsNullOrWhiteSpace($Image)) {
        if ([string]::IsNullOrWhiteSpace($ReleaseTag)) {
            throw 'GHCR deployment requires -Image or -ReleaseTag.'
        }

        if ([string]::IsNullOrWhiteSpace($GhcrOwner)) {
            $GhcrOwner = if ($ghcr -and $ghcr.owner) { [string]$ghcr.owner } else { '' }
        }
        Assert-EasyProtocolGhcrOwnerReady -Owner $GhcrOwner -SourceDescription 'GHCR owner'
        $Image = "$registry/$GhcrOwner/${serviceImageName}:$ReleaseTag"
    }

    $runtimeRoot = Split-Path -Parent $composeFile
    $deployGhcrScript = Join-Path $repoRoot 'deploy/service/base/scripts/deploy-ghcr-easy-protocol-service.ps1'
    $args = @(
        '-ConfigPath', $renderedConfigPath,
        '-RuntimeEnvPath', $renderedRuntimeEnvPath,
        '-Image', $Image,
        '-RuntimeRoot', $runtimeRoot,
        '-NetworkName', $networkName,
        '-ComposeSourcePath', $composeFile
    )
    if ($SkipPull) { $args += '-SkipPull' }

    Write-Host "Deploying service/base from GHCR image: $Image" -ForegroundColor Cyan
    Invoke-EasyProtocolExternalCommand -FilePath $deployGhcrScript -Arguments $args -FailureMessage 'deploy-ghcr-easy-protocol-service.ps1 failed'
    Write-Host 'service/base deployment finished.' -ForegroundColor Green
    return
}

Ensure-EasyProtocolExternalNetwork -NetworkName $networkName

if ($NoBuild) {
    docker compose -f $composeFile up -d
} else {
    docker compose -f $composeFile up -d --build
}

if ($LASTEXITCODE -ne 0) {
    throw "docker compose failed with exit code $LASTEXITCODE"
}

Write-Host 'service/base deployment finished.' -ForegroundColor Green
