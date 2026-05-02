[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,

    [Parameter(Mandatory = $true)]
    [string]$RuntimeEnvPath,

    [Parameter(Mandatory = $true)]
    [string]$Image,

    [string]$RuntimeRoot = 'C:\Users\Public\nas_home\AI\GameEditor\EasyProtocol\deploy\service\base',

    [string]$NetworkName = 'EasyAiMi',

    [string]$ComposeSourcePath = '',

    [string]$ContainerName = 'easyprotocol-service-base',

    [switch]$SkipPull
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    return $item.FullName
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code $LASTEXITCODE)"
    }
}

function Sync-ItemIfNeeded {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )

    $sourceResolved = [System.IO.Path]::GetFullPath($SourcePath)
    $destinationResolved = [System.IO.Path]::GetFullPath($DestinationPath)
    if ([string]::Equals($sourceResolved, $destinationResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
        return
    }

    Copy-Item -LiteralPath $sourceResolved -Destination $destinationResolved -Force
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Missing rendered service config: $ConfigPath"
}
if (-not (Test-Path -LiteralPath $RuntimeEnvPath)) {
    throw "Missing rendered runtime env file: $RuntimeEnvPath"
}
if ([string]::IsNullOrWhiteSpace($ComposeSourcePath)) {
    $ComposeSourcePath = Join-Path $PSScriptRoot '..\docker-compose.yaml'
}
if (-not (Test-Path -LiteralPath $ComposeSourcePath)) {
    throw "Missing compose template: $ComposeSourcePath"
}

$resolvedConfigPath = Resolve-FullPath -Path $ConfigPath
$resolvedRuntimeEnvPath = Resolve-FullPath -Path $RuntimeEnvPath
$resolvedComposeSourcePath = Resolve-FullPath -Path $ComposeSourcePath
$resolvedRuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$runtimeConfigDir = Join-Path $resolvedRuntimeRoot 'config'
$runtimeDataDir = Join-Path $resolvedRuntimeRoot 'data'
$runtimeComposePath = Join-Path $resolvedRuntimeRoot 'docker-compose.yaml'
$runtimeEnvFilePath = Join-Path $resolvedRuntimeRoot '.env'
$runtimeConfigPath = Join-Path $runtimeConfigDir 'config.yaml'
$runtimeRuntimeEnvPath = Join-Path $runtimeConfigDir 'runtime.env'

if ($PSCmdlet.ShouldProcess($resolvedRuntimeRoot, 'Prepare EasyProtocol GHCR runtime root')) {
    $null = New-Item -ItemType Directory -Force -Path $resolvedRuntimeRoot
    $null = New-Item -ItemType Directory -Force -Path $runtimeConfigDir
    $null = New-Item -ItemType Directory -Force -Path $runtimeDataDir

    Sync-ItemIfNeeded -SourcePath $resolvedComposeSourcePath -DestinationPath $runtimeComposePath
    Sync-ItemIfNeeded -SourcePath $resolvedConfigPath -DestinationPath $runtimeConfigPath
    Sync-ItemIfNeeded -SourcePath $resolvedRuntimeEnvPath -DestinationPath $runtimeRuntimeEnvPath

    @(
        "EASY_PROTOCOL_SERVICE_IMAGE=$Image"
        "EASY_PROTOCOL_SERVICE_NETWORK_NAME=$NetworkName"
        "EASY_PROTOCOL_SERVICE_CONTAINER_NAME=$ContainerName"
    ) | Set-Content -LiteralPath $runtimeEnvFilePath -Encoding utf8
}

& docker network inspect $NetworkName *> $null
if ($LASTEXITCODE -ne 0) {
    if ($PSCmdlet.ShouldProcess($NetworkName, 'Create Docker network for EasyProtocol runtime')) {
        Invoke-CheckedCommand -FilePath 'docker' -Arguments @('network', 'create', $NetworkName) -FailureMessage "Failed to create Docker network $NetworkName"
    }
}

if (-not $SkipPull) {
    if ($PSCmdlet.ShouldProcess($Image, 'Pull EasyProtocol GHCR image')) {
        Invoke-CheckedCommand -FilePath 'docker' -Arguments @('pull', $Image) -FailureMessage "Failed to pull $Image"
    }
}

$existingContainerId = (& docker ps -aq --filter "name=^$ContainerName$" 2>$null | Out-String).Trim()
if (-not [string]::IsNullOrWhiteSpace($existingContainerId)) {
    if ($PSCmdlet.ShouldProcess($ContainerName, 'Remove existing EasyProtocol container before compose redeploy')) {
        Invoke-CheckedCommand -FilePath 'docker' -Arguments @('rm', '-f', $ContainerName) -FailureMessage "Failed to remove existing $ContainerName container"
    }
}

if ($PSCmdlet.ShouldProcess($runtimeComposePath, 'Deploy EasyProtocol service container from GHCR')) {
    Invoke-CheckedCommand -FilePath 'docker' -Arguments @(
        'compose',
        '--env-file', $runtimeEnvFilePath,
        '-f', $runtimeComposePath,
        'up', '-d', '--remove-orphans'
    ) -FailureMessage 'Docker Compose deployment failed'
}

$deployedImage = (& docker inspect --format '{{.Config.Image}}' $ContainerName 2>$null)
if ($LASTEXITCODE -ne 0) {
    throw "Failed to inspect $ContainerName after deployment"
}
$deployedImage = ($deployedImage | Out-String).Trim()
if ($deployedImage -ne $Image -and -not $deployedImage.StartsWith("$Image@")) {
    throw "Deployed container image mismatch. Expected $Image, got $deployedImage"
}

Write-Host 'EasyProtocol GHCR runtime deployed successfully.' -ForegroundColor Green
Write-Host "Runtime root: $resolvedRuntimeRoot"
Write-Host "Image: $Image"
