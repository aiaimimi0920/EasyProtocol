param(
    [string]$SourceRoot = 'C:\Users\Public\nas_home\AI\GameEditor\ProtocolService'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedRepoRoot = (Resolve-Path $repoRoot).Path
$resolvedSourceRoot = (Resolve-Path $SourceRoot).Path

function Assert-TargetPathInsideRepo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($resolvedRepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside target repo: $resolved"
    }
    return $resolved
}

$mappings = @(
    @{ Source = Join-Path $resolvedSourceRoot 'repos\\EasyProtocol'; Target = Join-Path $resolvedRepoRoot 'service\\base' },
    @{ Source = Join-Path $resolvedSourceRoot 'repos\\PythonProtocol'; Target = Join-Path $resolvedRepoRoot 'providers\\python' },
    @{ Source = Join-Path $resolvedSourceRoot 'repos\\GolangProtocol'; Target = Join-Path $resolvedRepoRoot 'providers\\go' },
    @{ Source = Join-Path $resolvedSourceRoot 'repos\\JSProtocol'; Target = Join-Path $resolvedRepoRoot 'providers\\javascript' },
    @{ Source = Join-Path $resolvedSourceRoot 'repos\\RustProtocol'; Target = Join-Path $resolvedRepoRoot 'providers\\rust' },
    @{ Source = Join-Path $resolvedSourceRoot 'deploy\\EasyProtocol'; Target = Join-Path $resolvedRepoRoot 'deploy\\service\\base' },
    @{ Source = Join-Path $resolvedSourceRoot 'deploy\\PythonProtocol'; Target = Join-Path $resolvedRepoRoot 'deploy\\providers\\python' },
    @{ Source = Join-Path $resolvedSourceRoot 'deploy\\GolangProtocol'; Target = Join-Path $resolvedRepoRoot 'deploy\\providers\\go' },
    @{ Source = Join-Path $resolvedSourceRoot 'deploy\\JSProtocol'; Target = Join-Path $resolvedRepoRoot 'deploy\\providers\\javascript' },
    @{ Source = Join-Path $resolvedSourceRoot 'deploy\\RustProtocol'; Target = Join-Path $resolvedRepoRoot 'deploy\\providers\\rust' },
    @{ Source = Join-Path $resolvedSourceRoot 'deploy\\EasyStack'; Target = Join-Path $resolvedRepoRoot 'deploy\\stacks\\easy-protocol' }
)

foreach ($mapping in $mappings) {
    if (-not (Test-Path -LiteralPath $mapping.Source)) {
        throw "Missing source path: $($mapping.Source)"
    }

    $targetPath = Assert-TargetPathInsideRepo -Path $mapping.Target
    if (Test-Path -LiteralPath $targetPath) {
        Remove-Item -LiteralPath $targetPath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $targetPath | Out-Null

    Get-ChildItem -LiteralPath $mapping.Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Recurse -Force
    }
}

$cleanupTargets = @(
    'deploy\\service\\base\\config.yaml',
    'deploy\\stacks\\easy-protocol\\.env',
    'deploy\\stacks\\easy-protocol\\data',
    'providers\\rust\\target',
    'providers\\python\\src\\new_protocol_register\\success'
)

foreach ($relativePath in $cleanupTargets) {
    $absolutePath = Assert-TargetPathInsideRepo -Path (Join-Path $resolvedRepoRoot $relativePath)
    if (Test-Path -LiteralPath $absolutePath) {
        Remove-Item -LiteralPath $absolutePath -Recurse -Force
    }
}

Get-ChildItem -Path $resolvedRepoRoot -Recurse -Directory -Force |
    Where-Object { $_.Name -eq '__pycache__' } |
    ForEach-Object { Remove-Item -LiteralPath (Assert-TargetPathInsideRepo -Path $_.FullName) -Recurse -Force }

Get-ChildItem -Path $resolvedRepoRoot -Recurse -File -Force |
    Where-Object { $_.Extension -in '.pyc', '.pyo' } |
    ForEach-Object { Remove-Item -LiteralPath (Assert-TargetPathInsideRepo -Path $_.FullName) -Force }

Get-ChildItem -Path (Join-Path $resolvedRepoRoot 'providers\\python\\src\\protocol_runtime\\data') -Recurse -File -Filter *.json -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -LiteralPath (Assert-TargetPathInsideRepo -Path $_.FullName) -Force }

Write-Host 'ProtocolService sync replay complete.' -ForegroundColor Green
