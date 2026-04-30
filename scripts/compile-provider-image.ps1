param(
    [ValidateSet('python', 'go', 'javascript', 'rust')]
    [string]$Provider,
    [string]$ConfigPath = 'config.yaml',
    [string]$Platform = 'linux/amd64',
    [string]$Image = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')

$config = Read-EasyProtocolConfig -ConfigPath $ConfigPath
$providers = $config.providers
if ($null -eq $providers) {
    throw 'Missing providers section in config.yaml.'
}

$providerConfig = $providers.$Provider
if ($null -eq $providerConfig) {
    throw "Missing providers.$Provider section in config.yaml."
}

$repoRoot = Get-EasyProtocolRepoRoot
$dockerfileRelative = if ($providerConfig.dockerfile) { [string]$providerConfig.dockerfile } else { "deploy/providers/$Provider/Dockerfile" }
$configuredImage = if ($providerConfig.image) { [string]$providerConfig.image } else { "easyprotocol/${Provider}-protocol-service:local" }
$dockerfilePath = Join-Path $repoRoot $dockerfileRelative
$imageRef = if ([string]::IsNullOrWhiteSpace($Image)) { $configuredImage } else { $Image }

Write-Host "Building provider image [$Provider]: $imageRef" -ForegroundColor Cyan
docker build --platform $Platform -f $dockerfilePath -t $imageRef $repoRoot
if ($LASTEXITCODE -ne 0) {
    throw "Docker build failed with exit code $LASTEXITCODE"
}

Write-Host "Built provider image [$Provider]: $imageRef" -ForegroundColor Green
