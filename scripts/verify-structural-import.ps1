Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot

$requiredPaths = @(
    'service/base/cmd/easy_protocol/main.go',
    'providers/python/src/server.py',
    'providers/javascript/src/server.js',
    'providers/go/cmd/golang_protocol/main.go',
    'providers/rust/src/main.rs',
    'deploy/service/base/Dockerfile',
    'deploy/stacks/easy-protocol/docker-compose.yaml'
)

$excludedPaths = @(
    'providers/rust/target',
    'deploy/service/base/config.yaml',
    'deploy/stacks/easy-protocol/.env',
    'deploy/stacks/easy-protocol/data',
    'providers/python/src/new_protocol_register/success'
)

foreach ($relativePath in $requiredPaths) {
    $absolutePath = Join-Path $repoRoot $relativePath
    if (-not (Test-Path -LiteralPath $absolutePath)) {
        throw "Missing required path: $relativePath"
    }
}

foreach ($relativePath in $excludedPaths) {
    $absolutePath = Join-Path $repoRoot $relativePath
    if (Test-Path -LiteralPath $absolutePath) {
        throw "Excluded path still exists: $relativePath"
    }
}

$pycacheCount = @(
    Get-ChildItem -Path $repoRoot -Recurse -Directory -Force |
        Where-Object { $_.Name -eq '__pycache__' }
).Count
$pycCount = @(
    Get-ChildItem -Path $repoRoot -Recurse -File -Force |
        Where-Object { $_.Extension -in '.pyc', '.pyo' }
).Count

if ($pycacheCount -gt 0 -or $pycCount -gt 0) {
    throw "Python cache artifacts detected: __pycache__=$pycacheCount pyc=$pycCount"
}

Write-Host 'Structural import verification passed.' -ForegroundColor Green
