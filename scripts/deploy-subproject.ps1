param(
    [ValidateSet(
        'validate',
        'service-base',
        'service-base-ghcr',
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
    [switch]$SkipRender,
    [switch]$Push,
    [string]$ReleaseTag = '',
    [string]$Platform = 'linux/amd64',
    [ValidateSet('python', 'go', 'javascript', 'rust', 'all')]
    [string]$ProviderTarget = 'all',
    [string]$InstanceName = 'dyn01',
    [int]$GatewayHostPort = 29789,
    [int]$PythonManagerHostPort = 29103,
    [string]$GhcrOwner = '',
    [string]$Image = '',
    [switch]$SkipPull,
    [string]$RegisterOutputDirHost = '',
    [string]$RegisterTeamAuthDirHost = '',
    [string]$RegisterTeamLocalDirHost = '',
    [string]$MailboxServiceApiKey = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-common.ps1')

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
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'test-all.ps1') -Arguments @('-ConfigPath', $resolvedConfigPath) -FailureMessage 'test-all.ps1 failed'
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'verify-structural-import.ps1') -FailureMessage 'verify-structural-import.ps1 failed'
        break
    }
    'service-base' {
        $args = @('-ConfigPath', $resolvedConfigPath)
        if ($NoBuild) { $args += '-NoBuild' }
        if ($SkipRender) { $args += '-SkipRender' }
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'deploy-service-base.ps1') -Arguments $args -FailureMessage 'deploy-service-base.ps1 failed'
        break
    }
    'service-base-ghcr' {
        $args = @('-ConfigPath', $resolvedConfigPath, '-FromGhcr')
        if ($SkipRender) { $args += '-SkipRender' }
        if (-not [string]::IsNullOrWhiteSpace($ReleaseTag)) { $args += @('-ReleaseTag', $ReleaseTag) }
        if (-not [string]::IsNullOrWhiteSpace($GhcrOwner)) { $args += @('-GhcrOwner', $GhcrOwner) }
        if (-not [string]::IsNullOrWhiteSpace($Image)) { $args += @('-Image', $Image) }
        if ($SkipPull) { $args += '-SkipPull' }
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'deploy-service-base.ps1') -Arguments $args -FailureMessage 'deploy-service-base.ps1 failed'
        break
    }
    'easy-protocol' {
        $args = @('-ConfigPath', $resolvedConfigPath)
        if ($NoBuild) { $args += '-NoBuild' }
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'deploy-easyprotocol-stack.ps1') -Arguments $args -FailureMessage 'deploy-easyprotocol-stack.ps1 failed'
        break
    }
    'release-service-base' {
        $args = @('-ConfigPath', $resolvedConfigPath)
        if (-not [string]::IsNullOrWhiteSpace($ReleaseTag)) { $args += @('-Version', $ReleaseTag) }
        if (-not [string]::IsNullOrWhiteSpace($Platform)) { $args += @('-Platform', $Platform) }
        if ($Push) { $args += '-Push' }
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'deploy-easyprotocol-release.ps1') -Arguments $args -FailureMessage 'deploy-easyprotocol-release.ps1 failed'
        break
    }
    'build-service-base-image' {
        $args = @('-ConfigPath', $resolvedConfigPath, '-Platform', $Platform)
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'compile-service-base-image.ps1') -Arguments $args -FailureMessage 'compile-service-base-image.ps1 failed'
        break
    }
    'publish-service-base-image' {
        $args = @('-ConfigPath', $resolvedConfigPath, '-Platform', $Platform, '-Push')
        if (-not [string]::IsNullOrWhiteSpace($ReleaseTag)) { $args += @('-Version', $ReleaseTag) }
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'deploy-easyprotocol-release.ps1') -Arguments $args -FailureMessage 'deploy-easyprotocol-release.ps1 failed'
        break
    }
    'build-provider-images' {
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'publish-provider-images.ps1') -Arguments @(
            '-Target', $ProviderTarget,
            '-ConfigPath', $resolvedConfigPath,
            '-ReleaseTag', $ReleaseTag,
            '-Platform', $Platform
        ) -FailureMessage 'publish-provider-images.ps1 failed'
        break
    }
    'publish-provider-images' {
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'publish-provider-images.ps1') -Arguments @(
            '-Target', $ProviderTarget,
            '-ConfigPath', $resolvedConfigPath,
            '-ReleaseTag', $ReleaseTag,
            '-Platform', $Platform,
            '-Push'
        ) -FailureMessage 'publish-provider-images.ps1 failed'
        break
    }
    'sync-import' {
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'sync-from-protocolservice.ps1') -FailureMessage 'sync-from-protocolservice.ps1 failed'
        break
    }
    'isolated-instance' {
        $invokeParams = @{
            InstanceName = $InstanceName
            ConfigPath = $resolvedConfigPath
            GatewayHostPort = $GatewayHostPort
            PythonManagerHostPort = $PythonManagerHostPort
        }
        if (-not [string]::IsNullOrWhiteSpace($RegisterOutputDirHost)) { $invokeParams.RegisterOutputDirHost = $RegisterOutputDirHost }
        if (-not [string]::IsNullOrWhiteSpace($RegisterTeamAuthDirHost)) { $invokeParams.RegisterTeamAuthDirHost = $RegisterTeamAuthDirHost }
        if (-not [string]::IsNullOrWhiteSpace($RegisterTeamLocalDirHost)) { $invokeParams.RegisterTeamLocalDirHost = $RegisterTeamLocalDirHost }
        if (-not [string]::IsNullOrWhiteSpace($MailboxServiceApiKey)) { $invokeParams.MailboxServiceApiKey = $MailboxServiceApiKey }
        if ($NoBuild) { $invokeParams.NoBuild = $true }
        $args = @(
            '-InstanceName', $InstanceName,
            '-ConfigPath', $resolvedConfigPath,
            '-GatewayHostPort', [string]$GatewayHostPort,
            '-PythonManagerHostPort', [string]$PythonManagerHostPort
        )
        if (-not [string]::IsNullOrWhiteSpace($RegisterOutputDirHost)) { $args += @('-RegisterOutputDirHost', $RegisterOutputDirHost) }
        if (-not [string]::IsNullOrWhiteSpace($RegisterTeamAuthDirHost)) { $args += @('-RegisterTeamAuthDirHost', $RegisterTeamAuthDirHost) }
        if (-not [string]::IsNullOrWhiteSpace($RegisterTeamLocalDirHost)) { $args += @('-RegisterTeamLocalDirHost', $RegisterTeamLocalDirHost) }
        if (-not [string]::IsNullOrWhiteSpace($MailboxServiceApiKey)) { $args += @('-MailboxServiceApiKey', $MailboxServiceApiKey) }
        if ($NoBuild) { $args += '-NoBuild' }
        Invoke-EasyProtocolExternalCommand -FilePath (Join-Path $PSScriptRoot 'deploy-isolated-easyprotocol-instance.ps1') -Arguments $args -FailureMessage 'deploy-isolated-easyprotocol-instance.ps1 failed'
        break
    }
}

Write-Host "Done: $Project" -ForegroundColor Green
