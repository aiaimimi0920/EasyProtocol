Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-EasyProtocolRepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot '..\\..')).Path
}

function Resolve-EasyProtocolPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return (Resolve-Path $Path).Path
    }

    return (Resolve-Path (Join-Path (Get-EasyProtocolRepoRoot) $Path)).Path
}

function Assert-EasyProtocolPythonModule {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ModuleName,
        [Parameter(Mandatory = $true)]
        [string]$PackageName
    )

    & python -c "import $ModuleName" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Missing Python module '$ModuleName'. Install package '$PackageName' first."
    }
}

function Read-EasyProtocolConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConfigPath
    )

    Assert-EasyProtocolPythonModule -ModuleName 'yaml' -PackageName 'pyyaml'
    $resolvedConfigPath = Resolve-EasyProtocolPath -Path $ConfigPath
    $json = & python -c "import json, yaml, pathlib; print(json.dumps(yaml.safe_load(pathlib.Path(r'''$resolvedConfigPath''').read_text(encoding='utf-8')) or {}))"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read config file: $resolvedConfigPath"
    }
    return $json | ConvertFrom-Json
}
