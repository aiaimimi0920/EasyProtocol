param(
    [string]$ConfigPath = 'config.yaml',
    [switch]$NoBuild,
    [switch]$SkipRender,
    [switch]$FromGhcr,
    [string]$Image = '',
    [string]$ReleaseTag = '',
    [string]$GhcrOwner = '',
    [string]$ProviderImage = '',
    [string]$ProviderReleaseTag = '',
    [string]$RegisterOutputDirHost = '',
    [string]$RegisterTeamAuthDirHost = '',
    [string]$RegisterTeamLocalDirHost = '',
    [string]$MailboxServiceApiKey = '',
    [string]$EasyProxyApiKey = '',
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
$serviceImageName = 'easy-protocol-service'
$registry = if ($ghcr -and $ghcr.registry) { [string]$ghcr.registry } else { 'ghcr.io' }
$networkName = if ($stack -and $stack.networkName) { [string]$stack.networkName } else { 'EasyAiMi' }
$composeFile = Join-Path $repoRoot 'deploy/service/base/docker-compose.yaml'
$renderedConfigPath = if ([System.IO.Path]::IsPathRooted($ServiceOutput)) { $ServiceOutput } else { Join-Path $repoRoot $ServiceOutput }
$renderedRuntimeEnvPath = if ([System.IO.Path]::IsPathRooted($ServiceEnvOutput)) { $ServiceEnvOutput } else { Join-Path $repoRoot $ServiceEnvOutput }
$useGhcrDeploy = $FromGhcr -or -not [string]::IsNullOrWhiteSpace($Image) -or -not [string]::IsNullOrWhiteSpace($ReleaseTag)
$resolvedProviderImage = $ProviderImage
if ([string]::IsNullOrWhiteSpace($resolvedProviderImage) -and -not [string]::IsNullOrWhiteSpace($ProviderReleaseTag)) {
    $providerOwner = $GhcrOwner
    if ([string]::IsNullOrWhiteSpace($providerOwner)) {
        $providerOwner = if ($ghcr -and $ghcr.owner) { [string]$ghcr.owner } else { '' }
    }
    Assert-EasyProtocolGhcrOwnerReady -Owner $providerOwner -SourceDescription 'GHCR owner'
    $resolvedProviderImage = "$registry/$providerOwner/easy-protocol-python:$ProviderReleaseTag"
}

if (-not $SkipRender) {
    Write-Host 'Rendering service/base config...' -ForegroundColor Cyan
    $renderArgs = @(
        '-ConfigPath', $ConfigPath,
        '-ServiceBase',
        '-ServiceOutput', $ServiceOutput,
        '-ServiceEnvOutput', $ServiceEnvOutput
    )
    if (-not [string]::IsNullOrWhiteSpace($RegisterOutputDirHost)) { $renderArgs += @('-RegisterOutputDirHost', $RegisterOutputDirHost) }
    if (-not [string]::IsNullOrWhiteSpace($RegisterTeamAuthDirHost)) { $renderArgs += @('-RegisterTeamAuthDirHost', $RegisterTeamAuthDirHost) }
    if (-not [string]::IsNullOrWhiteSpace($RegisterTeamLocalDirHost)) { $renderArgs += @('-RegisterTeamLocalDirHost', $RegisterTeamLocalDirHost) }
    if (-not [string]::IsNullOrWhiteSpace($resolvedProviderImage)) { $renderArgs += @('-PythonProviderImage', $resolvedProviderImage) }
    Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $repoRoot 'scripts/render-derived-configs.ps1') -Arguments $renderArgs -FailureMessage 'render-derived-configs.ps1 failed'
}

if (-not (Test-Path -LiteralPath $renderedConfigPath)) {
    throw "Missing rendered service config: $renderedConfigPath"
}
if (-not (Test-Path -LiteralPath $renderedRuntimeEnvPath)) {
    throw "Missing rendered runtime env: $renderedRuntimeEnvPath"
}

if (-not [string]::IsNullOrWhiteSpace($MailboxServiceApiKey) -or -not [string]::IsNullOrWhiteSpace($EasyProxyApiKey)) {
    $patchArgs = @(
        (Join-Path $repoRoot 'scripts/patch-rendered-service-config.py'),
        '--config-path', $renderedConfigPath,
        '--runtime-env-path', $renderedRuntimeEnvPath
    )
    if (-not [string]::IsNullOrWhiteSpace($MailboxServiceApiKey)) { $patchArgs += @('--mailbox-service-api-key', $MailboxServiceApiKey) }
    if (-not [string]::IsNullOrWhiteSpace($EasyProxyApiKey)) { $patchArgs += @('--easy-proxy-api-key', $EasyProxyApiKey) }
    Invoke-EasyProtocolExternalCommand -FilePath 'python' -Arguments $patchArgs -FailureMessage 'patch-rendered-service-config.py failed'
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

    if ((-not $SkipPull) -and (-not [string]::IsNullOrWhiteSpace($resolvedProviderImage))) {
        Write-Host "Pulling managed provider image: $resolvedProviderImage" -ForegroundColor Cyan
        Invoke-EasyProtocolExternalCommand -FilePath 'docker' -Arguments @(
            'pull',
            $resolvedProviderImage
        ) -FailureMessage "docker pull failed for provider image: $resolvedProviderImage"
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
