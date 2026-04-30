param(
    [ValidateSet('python', 'go', 'javascript', 'rust', 'all')]
    [string]$Target = 'all',
    [string]$ConfigPath = 'config.yaml',
    [string]$ReleaseTag = 'providers-local',
    [string]$Platform = 'linux/amd64',
    [switch]$Push
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')

$config = Read-EasyProtocolConfig -ConfigPath $ConfigPath
$ghcr = if ($config.publishing) { $config.publishing.ghcr } else { $null }
$registry = if ($ghcr -and $ghcr.registry) {
    [string]$ghcr.registry
} elseif (-not [string]::IsNullOrWhiteSpace([string]$env:EASYPROTOCOL_PUBLISH_GHCR_REGISTRY)) {
    [string]$env:EASYPROTOCOL_PUBLISH_GHCR_REGISTRY
} else {
    'ghcr.io'
}
$owner = if ($ghcr -and $ghcr.owner) {
    [string]$ghcr.owner
} elseif (-not [string]::IsNullOrWhiteSpace([string]$env:EASYPROTOCOL_PUBLISH_GHCR_OWNER)) {
    [string]$env:EASYPROTOCOL_PUBLISH_GHCR_OWNER
} elseif (-not [string]::IsNullOrWhiteSpace([string]$env:GITHUB_REPOSITORY_OWNER)) {
    [string]$env:GITHUB_REPOSITORY_OWNER
} else {
    ''
}
$owner = [string]$owner
if (-not [string]::IsNullOrWhiteSpace($owner)) {
    $owner = $owner.ToLowerInvariant()
}

$targets = if ($Target -eq 'all') { @('python', 'go', 'javascript', 'rust') } else { @($Target) }

foreach ($provider in $targets) {
    $providerConfig = $config.providers.$provider
    if ($null -eq $providerConfig) {
        throw "Missing providers.$provider section in config.yaml."
    }

    $configuredImage = if ($providerConfig.image) { [string]$providerConfig.image } else { "easyprotocol/${provider}-protocol-service:local" }
    if ([string]::IsNullOrWhiteSpace($owner)) {
        $imageRef = $configuredImage
    } else {
        $imageName = [string]($configuredImage -replace '^.+/', '' -replace ':.+$', '')
        $imageRef = "$registry/$owner/${imageName}:$ReleaseTag"
    }

    & (Join-Path $PSScriptRoot 'compile-provider-image.ps1') `
        -Provider $provider `
        -ConfigPath $ConfigPath `
        -Platform $Platform `
        -Image $imageRef
    if ($LASTEXITCODE -ne 0) {
        throw "compile-provider-image.ps1 failed for $provider with exit code $LASTEXITCODE"
    }

    if ($Push) {
        docker push $imageRef
        if ($LASTEXITCODE -ne 0) {
            throw "docker push failed for $provider with exit code $LASTEXITCODE"
        }
    }
}

Write-Host "Provider publish flow finished for target: $Target" -ForegroundColor Green
