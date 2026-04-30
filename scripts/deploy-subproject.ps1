param(
    [ValidateSet(
        'validate',
        'service-base',
        'easy-protocol',
        'release-service-base',
        'build-service-base-image',
        'publish-service-base-image',
        'build-provider-images',
        'publish-provider-images',
        'sync-import',
        'isolated-instance'
    )]
    [string]$Project,
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config.yaml'),
    [switch]$InitConfig,
    [switch]$NoBuild,
    [switch]$Push,
    [string]$ReleaseTag = '',
    [string]$Platform = 'linux/amd64',
    [ValidateSet('python', 'go', 'javascript', 'rust', 'all')]
    [string]$ProviderTarget = 'all',
    [string]$InstanceName = 'dyn01',
    [int]$GatewayHostPort = 29789,
    [int]$PythonManagerHostPort = 29103
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-ConfigPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }

    return (Join-Path (Split-Path -Parent $PSScriptRoot) $Path)
}

$resolvedConfigPath = Resolve-ConfigPath -Path $ConfigPath

if (-not (Test-Path -LiteralPath $resolvedConfigPath)) {
    if (-not $InitConfig) {
        throw "Missing config file: $resolvedConfigPath. Run scripts/init-config.ps1 first or pass -InitConfig."
    }
    & (Join-Path $PSScriptRoot 'init-config.ps1') -ConfigPath $resolvedConfigPath
}

switch ($Project) {
    'validate' {
        & (Join-Path $PSScriptRoot 'test-all.ps1') -ConfigPath $resolvedConfigPath
        & (Join-Path $PSScriptRoot 'verify-structural-import.ps1')
        break
    }
    'service-base' {
        $args = @('-ConfigPath', $resolvedConfigPath)
        if ($NoBuild) { $args += '-NoBuild' }
        & (Join-Path $PSScriptRoot 'deploy-service-base.ps1') @args
        break
    }
    'easy-protocol' {
        $args = @('-ConfigPath', $resolvedConfigPath)
        if ($NoBuild) { $args += '-NoBuild' }
        & (Join-Path $PSScriptRoot 'deploy-easyprotocol-stack.ps1') @args
        break
    }
    'release-service-base' {
        $args = @('-ConfigPath', $resolvedConfigPath)
        if (-not [string]::IsNullOrWhiteSpace($ReleaseTag)) { $args += @('-Version', $ReleaseTag) }
        if (-not [string]::IsNullOrWhiteSpace($Platform)) { $args += @('-Platform', $Platform) }
        if ($Push) { $args += '-Push' }
        & (Join-Path $PSScriptRoot 'deploy-easyprotocol-release.ps1') @args
        break
    }
    'build-service-base-image' {
        $args = @('-ConfigPath', $resolvedConfigPath, '-Platform', $Platform)
        & (Join-Path $PSScriptRoot 'compile-service-base-image.ps1') @args
        break
    }
    'publish-service-base-image' {
        $args = @('-ConfigPath', $resolvedConfigPath, '-Platform', $Platform, '-Push')
        if (-not [string]::IsNullOrWhiteSpace($ReleaseTag)) { $args += @('-Version', $ReleaseTag) }
        & (Join-Path $PSScriptRoot 'deploy-easyprotocol-release.ps1') @args
        break
    }
    'build-provider-images' {
        & (Join-Path $PSScriptRoot 'publish-provider-images.ps1') `
            -Target $ProviderTarget `
            -ConfigPath $resolvedConfigPath `
            -ReleaseTag $ReleaseTag `
            -Platform $Platform
        break
    }
    'publish-provider-images' {
        & (Join-Path $PSScriptRoot 'publish-provider-images.ps1') `
            -Target $ProviderTarget `
            -ConfigPath $resolvedConfigPath `
            -ReleaseTag $ReleaseTag `
            -Platform $Platform `
            -Push
        break
    }
    'sync-import' {
        & (Join-Path $PSScriptRoot 'sync-from-protocolservice.ps1')
        break
    }
    'isolated-instance' {
        $invokeParams = @{
            InstanceName = $InstanceName
            ConfigPath = $resolvedConfigPath
            GatewayHostPort = $GatewayHostPort
            PythonManagerHostPort = $PythonManagerHostPort
        }
        if ($NoBuild) { $invokeParams.NoBuild = $true }
        & (Join-Path $PSScriptRoot 'deploy-isolated-easyprotocol-instance.ps1') @invokeParams
        break
    }
}

if ($LASTEXITCODE -ne 0) {
    throw "Project command failed: $Project"
}

Write-Host "Done: $Project" -ForegroundColor Green
