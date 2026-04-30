param(
    [string]$ConfigPath = 'config.yaml',
    [string]$Version = 'release-local',
    [string]$Platform = 'linux/amd64',
    [switch]$Push,
    [switch]$SkipSmoke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')

$repoRoot = Split-Path -Parent $PSScriptRoot
$config = Read-EasyProtocolConfig -ConfigPath $ConfigPath
$serviceBase = $config.serviceBase
if ($null -eq $serviceBase) {
    throw 'Missing serviceBase section in config.yaml.'
}

$publishing = $config.publishing
$ghcr = if ($null -ne $publishing) { $publishing.ghcr } else { $null }
$registry = if ($null -ne $ghcr -and $ghcr.registry) { [string]$ghcr.registry } else { 'ghcr.io' }
$owner = if ($null -ne $ghcr -and $ghcr.owner) { [string]$ghcr.owner } else { '' }
$configuredImage = if ($serviceBase.image) { [string]$serviceBase.image } else { 'easyprotocol/easy-protocol-service:local' }

if ([string]::IsNullOrWhiteSpace($owner)) {
    $imageRef = $configuredImage
} else {
    $imageRef = "$registry/$owner/easy-protocol-service:$Version"
}

Write-Host 'Rendering service/base config...' -ForegroundColor Cyan
& (Join-Path $repoRoot 'scripts/render-derived-configs.ps1') -ConfigPath $ConfigPath -ServiceBase
if ($LASTEXITCODE -ne 0) {
    throw "render-derived-configs.ps1 failed with exit code $LASTEXITCODE"
}

& (Join-Path $repoRoot 'scripts/compile-service-base-image.ps1') `
    -ConfigPath $ConfigPath `
    -Platform $Platform `
    -Image $imageRef
if ($LASTEXITCODE -ne 0) {
    throw "compile-service-base-image.ps1 failed with exit code $LASTEXITCODE"
}

if (-not $SkipSmoke) {
    & (Join-Path $repoRoot 'deploy/service/base/scripts/smoke-easy-protocol-docker-api.ps1') `
        -Image $imageRef `
        -ConfigPath 'deploy/service/base/config/config.yaml'
    if ($LASTEXITCODE -ne 0) {
        throw "smoke-easy-protocol-docker-api.ps1 failed with exit code $LASTEXITCODE"
    }
}

if ($Push) {
    docker push $imageRef
    if ($LASTEXITCODE -ne 0) {
        throw "docker push failed with exit code $LASTEXITCODE"
    }
}

Write-Host "EasyProtocol release flow finished: $imageRef" -ForegroundColor Green

