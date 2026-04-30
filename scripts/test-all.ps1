param(
    [string]$ConfigPath = 'config.yaml'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceBaseDir = Join-Path $repoRoot 'service/base'
$goProviderDir = Join-Path $repoRoot 'providers/go'
$jsProviderFile = Join-Path $repoRoot 'providers/javascript/src/server.js'
$pythonSrcDir = Join-Path $repoRoot 'providers/python/src'
$pythonSharedDir = Join-Path $repoRoot 'providers/python/python_shared/src'
$rustManifestPath = Join-Path $repoRoot 'providers/rust/Cargo.toml'
$rustTargetDir = Join-Path $repoRoot 'providers/rust/target'
$resolvedConfigPath = if ([System.IO.Path]::IsPathRooted($ConfigPath)) {
    $ConfigPath
} else {
    Join-Path $repoRoot $ConfigPath
}

if (-not (Test-Path -LiteralPath $resolvedConfigPath)) {
    throw "Missing config file: $resolvedConfigPath"
}

try {
    Write-Host 'Rendering derived configs...' -ForegroundColor Cyan
    & (Join-Path $repoRoot 'scripts/render-derived-configs.ps1') -ConfigPath $resolvedConfigPath -ServiceBase -EasyProtocol
    if ($LASTEXITCODE -ne 0) {
        throw "render-derived-configs.ps1 failed with exit code $LASTEXITCODE"
    }

    Write-Host 'Running service/base Go tests...' -ForegroundColor Cyan
    Push-Location $serviceBaseDir
    try {
        & go test ./...
        if ($LASTEXITCODE -ne 0) {
            throw "service/base go test failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }

    Write-Host 'Running providers/go Go tests...' -ForegroundColor Cyan
    Push-Location $goProviderDir
    try {
        & go test ./...
        if ($LASTEXITCODE -ne 0) {
            throw "providers/go go test failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }

    Write-Host 'Checking providers/javascript syntax...' -ForegroundColor Cyan
    & node --check $jsProviderFile
    if ($LASTEXITCODE -ne 0) {
        throw "providers/javascript syntax check failed with exit code $LASTEXITCODE"
    }

    Write-Host 'Compiling providers/python sources...' -ForegroundColor Cyan
    & python -m compileall $pythonSrcDir $pythonSharedDir
    if ($LASTEXITCODE -ne 0) {
        throw "providers/python compileall failed with exit code $LASTEXITCODE"
    }

    Write-Host 'Running python-protocol-manager smoke...' -ForegroundColor Cyan
    & python (Join-Path $repoRoot 'scripts/test-python-protocol-manager.py') --repo-root $repoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "test-python-protocol-manager.py failed with exit code $LASTEXITCODE"
    }

    Write-Host 'Running providers/rust cargo check...' -ForegroundColor Cyan
    & cargo check --manifest-path $rustManifestPath
    if ($LASTEXITCODE -ne 0) {
        throw "providers/rust cargo check failed with exit code $LASTEXITCODE"
    }

    Write-Host 'Repository validation finished.' -ForegroundColor Green
} finally {
    Get-ChildItem -Path $repoRoot -Recurse -Directory -Force |
        Where-Object { $_.Name -eq '__pycache__' } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }

    Get-ChildItem -Path $repoRoot -Recurse -File -Force |
        Where-Object { $_.Extension -in '.pyc', '.pyo' } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

    if (Test-Path -LiteralPath $rustTargetDir) {
        Remove-Item -LiteralPath $rustTargetDir -Recurse -Force
    }
}
