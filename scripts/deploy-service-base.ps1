param(
    [string]$ConfigPath = 'config.yaml',
    [switch]$NoBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot 'lib/easyprotocol-network.ps1')

Write-Host 'Rendering service/base config...' -ForegroundColor Cyan
& (Join-Path $repoRoot 'scripts/render-derived-configs.ps1') -ConfigPath $ConfigPath -ServiceBase
if ($LASTEXITCODE -ne 0) {
    throw "render-derived-configs.ps1 failed with exit code $LASTEXITCODE"
}

Ensure-EasyProtocolExternalNetwork -NetworkName 'EasyAiMi'

$composeFile = Join-Path $repoRoot 'deploy/service/base/docker-compose.yaml'
if ($NoBuild) {
    docker compose -f $composeFile up -d
} else {
    docker compose -f $composeFile up -d --build
}

if ($LASTEXITCODE -ne 0) {
    throw "docker compose failed with exit code $LASTEXITCODE"
}

Write-Host 'service/base deployment finished.' -ForegroundColor Green

