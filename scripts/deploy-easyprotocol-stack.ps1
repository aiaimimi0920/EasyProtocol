param(
    [string]$ConfigPath = 'config.yaml',
    [switch]$NoBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot 'lib/easyprotocol-network.ps1')

Write-Host 'Rendering easy-protocol stack config...' -ForegroundColor Cyan
& (Join-Path $repoRoot 'scripts/render-derived-configs.ps1') -ConfigPath $ConfigPath -EasyProtocol
if ($LASTEXITCODE -ne 0) {
    throw "render-derived-configs.ps1 failed with exit code $LASTEXITCODE"
}

Ensure-EasyProtocolExternalNetwork -NetworkName 'EasyAiMi'

$stackRoot = Join-Path $repoRoot 'deploy/stacks/easy-protocol'
$generatedDataRoot = Join-Path $stackRoot 'data/easy-protocol'
New-Item -ItemType Directory -Force -Path $generatedDataRoot | Out-Null

$composeFile = Join-Path $stackRoot 'docker-compose.yaml'
if ($NoBuild) {
    docker compose -f $composeFile up -d
} else {
    docker compose -f $composeFile up -d --build
}

if ($LASTEXITCODE -ne 0) {
    throw "docker compose failed with exit code $LASTEXITCODE"
}

Write-Host 'easy-protocol stack deployment finished.' -ForegroundColor Green

