param(
    [string]$ConfigPath = 'config.yaml',
    [string]$Platform = 'linux/amd64',
    [string]$Image = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')

$config = Read-EasyProtocolConfig -ConfigPath $ConfigPath
$serviceBase = $config.serviceBase
if ($null -eq $serviceBase) {
    throw 'Missing serviceBase section in config.yaml.'
}

$repoRoot = Get-EasyProtocolRepoRoot
$contextRelative = if ($serviceBase.context) { [string]$serviceBase.context } else { '.' }
$dockerfileRelative = if ($serviceBase.dockerfile) { [string]$serviceBase.dockerfile } else { 'deploy/service/base/Dockerfile' }
$configuredImage = if ($serviceBase.image) { [string]$serviceBase.image } else { 'easyprotocol/easy-protocol-service:local' }

$contextPath = Join-Path $repoRoot $contextRelative
$dockerfilePath = Join-Path $repoRoot $dockerfileRelative
$imageRef = if ([string]::IsNullOrWhiteSpace($Image)) { $configuredImage } else { $Image }

Write-Host "Building service/base image: $imageRef" -ForegroundColor Cyan
docker build --platform $Platform -f $dockerfilePath -t $imageRef $contextPath
if ($LASTEXITCODE -ne 0) {
    throw "Docker build failed with exit code $LASTEXITCODE"
}

Write-Host "Built image: $imageRef" -ForegroundColor Green

