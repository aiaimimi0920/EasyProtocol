param(
    [ValidateSet(
        "service-base",
        "service-base-ghcr",
        "easy-protocol",
        "release-service-base",
        "build-service-base-image",
        "publish-service-base-image",
        "build-provider-images",
        "publish-provider-images",
        "sync-import",
        "isolated-instance",
        "isolated-instance-ghcr"
    )]
    [string]$Project = "easy-protocol",
    [string]$ConfigPath = "config.yaml",
    [string]$ImportCode = "",
    [string]$BootstrapFile = "",
    [switch]$NoBuild,
    [switch]$SkipRender,
    [switch]$Push,
    [string]$ReleaseTag = "",
    [string]$Platform = "linux/amd64",
    [ValidateSet("python", "go", "javascript", "rust", "all")]
    [string]$ProviderTarget = "all",
    [string]$InstanceName = "dyn01",
    [int]$GatewayHostPort = 29789,
    [int]$PythonManagerHostPort = 29103,
    [string]$GhcrOwner = "",
    [string]$Image = "",
    [string]$ProviderImage = "",
    [string]$ProviderReleaseTag = "",
    [switch]$SkipPull,
    [string]$RegisterOutputDirHost = "",
    [string]$ProtocolBridgeVolumeName = "",
    [string]$RegisterTeamAuthDirHost = "",
    [string]$RegisterTeamLocalDirHost = "",
    [string]$MailboxServiceApiKey = "",
    [string]$EasyProxyApiKey = "",
    [string]$RepoOwner = "aiaimimi0920",
    [string]$RepoName = "EasyProtocol",
    [string]$RepoRef = "main",
    [ValidateSet("branch", "tag")]
    [string]$RepoRefKind = "branch",
    [string]$RepoArchiveUrl = "",
    [string]$RepoCacheRoot = "",
    [switch]$ForceRefreshRepo,
    [string]$EasyBrowserRepoOwner = "aiaimimi0920",
    [string]$EasyBrowserRepoRef = "main",
    [ValidateSet("branch", "tag")]
    [string]$EasyBrowserRepoRefKind = "branch",
    [string]$EasyBrowserRepoArchiveUrl = "",
    [string]$EasyBrowserRepoCacheRoot = "",
    [switch]$ResolveRepoOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-AbsolutePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$BaseDir
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $BaseDir $Path))
}

function Test-RepoLayout {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string[]]$RequiredRelativePaths
    )

    foreach ($relativePath in $RequiredRelativePaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $relativePath))) {
            return $false
        }
    }
    return $true
}

function Get-RepoArchiveUrlValue {
    param(
        [string]$Owner,
        [string]$Name,
        [string]$Ref,
        [string]$Kind,
        [string]$ExplicitUrl
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitUrl)) {
        return $ExplicitUrl
    }
    if ($Kind -eq "tag") {
        return "https://codeload.github.com/$Owner/$Name/zip/refs/tags/$Ref"
    }
    return "https://codeload.github.com/$Owner/$Name/zip/refs/heads/$Ref"
}

function Ensure-RepoRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LauncherRoot,
        [Parameter(Mandatory = $true)]
        [string]$Owner,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Ref,
        [Parameter(Mandatory = $true)]
        [string]$RefKind,
        [Parameter(Mandatory = $true)]
        [string[]]$RequiredRelativePaths,
        [string]$ArchiveUrl = "",
        [string]$CacheRoot = "",
        [switch]$ForceRefresh
    )

    if (Test-RepoLayout -Root $LauncherRoot -RequiredRelativePaths $RequiredRelativePaths) {
        return [pscustomobject]@{
            RepoRoot = $LauncherRoot
            Source = "local"
            ArchiveUrl = $null
        }
    }

    $sanitizedRef = ($Ref -replace '[^A-Za-z0-9._-]', '_')
    $resolvedCacheRoot = if ([string]::IsNullOrWhiteSpace($CacheRoot)) {
        Join-Path $LauncherRoot ".repo-cache\$Name-$RefKind-$sanitizedRef"
    } else {
        Resolve-AbsolutePath -Path $CacheRoot -BaseDir $LauncherRoot
    }
    $archiveUrlValue = Get-RepoArchiveUrlValue -Owner $Owner -Name $Name -Ref $Ref -Kind $RefKind -ExplicitUrl $ArchiveUrl
    $repoRoot = Join-Path $resolvedCacheRoot "repo"

    if ($ForceRefresh -and (Test-Path -LiteralPath $resolvedCacheRoot)) {
        Remove-Item -LiteralPath $resolvedCacheRoot -Recurse -Force
    }

    if (-not (Test-RepoLayout -Root $repoRoot -RequiredRelativePaths $RequiredRelativePaths)) {
        New-Item -ItemType Directory -Force -Path $resolvedCacheRoot | Out-Null
        $archivePath = Join-Path $resolvedCacheRoot "$Name-$sanitizedRef.zip"
        $expandedRoot = Join-Path $resolvedCacheRoot "expanded"

        if (Test-Path -LiteralPath $archivePath) {
            Remove-Item -LiteralPath $archivePath -Force
        }
        if (Test-Path -LiteralPath $expandedRoot) {
            Remove-Item -LiteralPath $expandedRoot -Recurse -Force
        }
        if (Test-Path -LiteralPath $repoRoot) {
            Remove-Item -LiteralPath $repoRoot -Recurse -Force
        }

        Write-Host "[deploy-host] downloading repository archive: $archiveUrlValue" -ForegroundColor Cyan
        $previousProgressPreference = $global:ProgressPreference
        $global:ProgressPreference = "SilentlyContinue"
        try {
            Invoke-WebRequest -Uri $archiveUrlValue -OutFile $archivePath
        } finally {
            $global:ProgressPreference = $previousProgressPreference
        }
        Expand-Archive -LiteralPath $archivePath -DestinationPath $expandedRoot -Force

        $extractedRoot = Get-ChildItem -LiteralPath $expandedRoot -Directory | Select-Object -First 1
        if ($null -eq $extractedRoot) {
            throw "Repository archive did not contain an extractable root directory: $archiveUrlValue"
        }

        Move-Item -LiteralPath $extractedRoot.FullName -Destination $repoRoot
    }

    if (-not (Test-RepoLayout -Root $repoRoot -RequiredRelativePaths $RequiredRelativePaths)) {
        throw "Bootstrapped repository root is missing required paths: $repoRoot"
    }

    return [pscustomobject]@{
        RepoRoot = $repoRoot
        Source = "bootstrapped"
        ArchiveUrl = $archiveUrlValue
    }
}

$launcherRoot = Split-Path -Parent $PSCommandPath
$repoInfo = Ensure-RepoRoot `
    -LauncherRoot $launcherRoot `
    -Owner $RepoOwner `
    -Name $RepoName `
    -Ref $RepoRef `
    -RefKind $RepoRefKind `
    -RequiredRelativePaths @("README.md", "scripts\deploy-subproject.ps1", "config.example.yaml") `
    -ArchiveUrl $RepoArchiveUrl `
    -CacheRoot $RepoCacheRoot `
    -ForceRefresh:$ForceRefreshRepo

if ($ResolveRepoOnly) {
    [pscustomobject]@{
        LauncherRoot = $launcherRoot
        RepoRoot = $repoInfo.RepoRoot
        Source = $repoInfo.Source
        ArchiveUrl = $repoInfo.ArchiveUrl
    } | Format-List
    return
}

$repoRoot = $repoInfo.RepoRoot
$resolvedConfigPath = Resolve-AbsolutePath -Path $ConfigPath -BaseDir $launcherRoot
$configExamplePath = Resolve-AbsolutePath -Path "config.example.yaml" -BaseDir $repoRoot
$deployScript = Resolve-AbsolutePath -Path "scripts\deploy-subproject.ps1" -BaseDir $repoRoot
$writeBootstrapScript = Resolve-AbsolutePath -Path "scripts\write-service-base-r2-bootstrap.ps1" -BaseDir $repoRoot
$patchRenderedServiceConfigScript = Resolve-AbsolutePath -Path "scripts\patch-rendered-service-config.py" -BaseDir $repoRoot
$bootstrapRuntimeScript = Resolve-AbsolutePath -Path "deploy\service\base\bootstrap-service-config.py" -BaseDir $repoRoot
$bootstrapHostPath = Resolve-AbsolutePath -Path "deploy\service\base\config\bootstrap\r2-bootstrap.json" -BaseDir $repoRoot
$serviceBaseConfigPath = Resolve-AbsolutePath -Path "deploy\service\base\config\config.yaml" -BaseDir $repoRoot
$serviceBaseRuntimeEnvPath = Resolve-AbsolutePath -Path "deploy\service\base\config\runtime.env" -BaseDir $repoRoot

if ($Project -in @("isolated-instance", "build-provider-images", "publish-provider-images")) {
    $resolvedEasyBrowserRepoRoot = $env:EASYBROWSER_REPO_ROOT
    if ([string]::IsNullOrWhiteSpace($resolvedEasyBrowserRepoRoot)) {
        $easyBrowserInfo = Ensure-RepoRoot `
            -LauncherRoot $launcherRoot `
            -Owner $EasyBrowserRepoOwner `
            -Name "EasyBrowser" `
            -Ref $EasyBrowserRepoRef `
            -RefKind $EasyBrowserRepoRefKind `
            -RequiredRelativePaths @("README.md", "runtimes\chrome", "deploy\service\base\Dockerfile") `
            -ArchiveUrl $EasyBrowserRepoArchiveUrl `
            -CacheRoot $EasyBrowserRepoCacheRoot `
            -ForceRefresh:$ForceRefreshRepo
        $resolvedEasyBrowserRepoRoot = $easyBrowserInfo.RepoRoot
    }
    $env:EASYBROWSER_REPO_ROOT = $resolvedEasyBrowserRepoRoot
}

if (-not (Test-Path -LiteralPath $resolvedConfigPath)) {
    Copy-Item -LiteralPath $configExamplePath -Destination $resolvedConfigPath
    Write-Host "[deploy-host] created config file from template: $resolvedConfigPath" -ForegroundColor Yellow
}

if (-not [string]::IsNullOrWhiteSpace($ImportCode) -and -not [string]::IsNullOrWhiteSpace($BootstrapFile)) {
    throw 'Specify either ImportCode or BootstrapFile, not both.'
}

$shouldBootstrapFromImport = -not [string]::IsNullOrWhiteSpace($ImportCode) -or -not [string]::IsNullOrWhiteSpace($BootstrapFile)
if ($shouldBootstrapFromImport -and $Project -notin @('service-base', 'service-base-ghcr', 'easy-protocol', 'isolated-instance', 'isolated-instance-ghcr')) {
    throw "Project '$Project' does not support ImportCode/BootstrapFile bootstrap."
}

$resolvedGhcrOwner = $GhcrOwner
$requiresGhcrOwnerDefault = `
    ($Project -in @('service-base-ghcr', 'isolated-instance-ghcr')) -or `
    (-not [string]::IsNullOrWhiteSpace($ReleaseTag)) -or `
    (-not [string]::IsNullOrWhiteSpace($ProviderReleaseTag))
if ([string]::IsNullOrWhiteSpace($resolvedGhcrOwner) -and $requiresGhcrOwnerDefault) {
    $resolvedGhcrOwner = $RepoOwner
}

if ($shouldBootstrapFromImport) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bootstrapHostPath) | Out-Null
    if (-not [string]::IsNullOrWhiteSpace($ImportCode)) {
        & powershell -ExecutionPolicy Bypass -File $writeBootstrapScript `
            -ImportCode $ImportCode `
            -OutputPath $bootstrapHostPath
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to materialize bootstrap file from import code with exit code $LASTEXITCODE"
        }
    } else {
        $resolvedBootstrapFile = Resolve-AbsolutePath -Path $BootstrapFile -BaseDir $launcherRoot
        if (-not (Test-Path -LiteralPath $resolvedBootstrapFile)) {
            throw "Bootstrap file not found: $resolvedBootstrapFile"
        }
        Copy-Item -LiteralPath $resolvedBootstrapFile -Destination $bootstrapHostPath -Force
    }

    & python $bootstrapRuntimeScript `
        --bootstrap-path $bootstrapHostPath `
        --config-path $serviceBaseConfigPath `
        --runtime-env-path $serviceBaseRuntimeEnvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to bootstrap EasyProtocol service/base config with exit code $LASTEXITCODE"
    }

    $resolvedProviderImage = $ProviderImage
    if ([string]::IsNullOrWhiteSpace($resolvedProviderImage) -and -not [string]::IsNullOrWhiteSpace($ProviderReleaseTag)) {
        $resolvedProviderImage = "ghcr.io/$resolvedGhcrOwner/easy-protocol-python:$ProviderReleaseTag"
    }

    $shouldPatchBootstrappedServiceConfig = `
        (-not [string]::IsNullOrWhiteSpace($resolvedProviderImage)) -or `
        (-not [string]::IsNullOrWhiteSpace($RegisterOutputDirHost)) -or `
        (-not [string]::IsNullOrWhiteSpace($ProtocolBridgeVolumeName)) -or `
        (-not [string]::IsNullOrWhiteSpace($RegisterTeamAuthDirHost)) -or `
        (-not [string]::IsNullOrWhiteSpace($RegisterTeamLocalDirHost)) -or `
        (-not [string]::IsNullOrWhiteSpace($MailboxServiceApiKey)) -or `
        (-not [string]::IsNullOrWhiteSpace($EasyProxyApiKey))

    if ($shouldPatchBootstrappedServiceConfig) {
        $patchArgs = @(
            $patchRenderedServiceConfigScript,
            "--config-path", $serviceBaseConfigPath,
            "--runtime-env-path", $serviceBaseRuntimeEnvPath
        )
        if (-not [string]::IsNullOrWhiteSpace($resolvedProviderImage)) { $patchArgs += @("--python-provider-image", $resolvedProviderImage) }
        if (-not [string]::IsNullOrWhiteSpace($RegisterOutputDirHost)) { $patchArgs += @("--register-output-dir-host", $RegisterOutputDirHost) }
        if (-not [string]::IsNullOrWhiteSpace($ProtocolBridgeVolumeName)) { $patchArgs += @("--protocol-bridge-volume-name", $ProtocolBridgeVolumeName) }
        if (-not [string]::IsNullOrWhiteSpace($RegisterTeamAuthDirHost)) { $patchArgs += @("--register-team-auth-dir-host", $RegisterTeamAuthDirHost) }
        if (-not [string]::IsNullOrWhiteSpace($RegisterTeamLocalDirHost)) { $patchArgs += @("--register-team-local-dir-host", $RegisterTeamLocalDirHost) }
        if (-not [string]::IsNullOrWhiteSpace($MailboxServiceApiKey)) { $patchArgs += @("--mailbox-service-api-key", $MailboxServiceApiKey) }
        if (-not [string]::IsNullOrWhiteSpace($EasyProxyApiKey)) { $patchArgs += @("--easy-proxy-api-key", $EasyProxyApiKey) }
        & python @patchArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to patch bootstrapped EasyProtocol service/base config with exit code $LASTEXITCODE"
        }
    }
}

$args = @(
    "-ExecutionPolicy", "Bypass",
    "-File", $deployScript,
    "-Project", $Project,
    "-ConfigPath", $resolvedConfigPath
)
if (-not (Test-Path -LiteralPath $resolvedConfigPath)) {
    $args += "-InitConfig"
}
if ($NoBuild) { $args += "-NoBuild" }
if ($SkipRender -or $shouldBootstrapFromImport) { $args += "-SkipRender" }
if ($Push) { $args += "-Push" }
if (-not [string]::IsNullOrWhiteSpace($ReleaseTag)) { $args += @("-ReleaseTag", $ReleaseTag) }
if (-not [string]::IsNullOrWhiteSpace($Platform)) { $args += @("-Platform", $Platform) }
if (-not [string]::IsNullOrWhiteSpace($ProviderTarget)) { $args += @("-ProviderTarget", $ProviderTarget) }
if (-not [string]::IsNullOrWhiteSpace($InstanceName)) { $args += @("-InstanceName", $InstanceName) }
if ($GatewayHostPort -gt 0) { $args += @("-GatewayHostPort", [string]$GatewayHostPort) }
if ($PythonManagerHostPort -gt 0) { $args += @("-PythonManagerHostPort", [string]$PythonManagerHostPort) }
if (-not [string]::IsNullOrWhiteSpace($resolvedGhcrOwner)) { $args += @("-GhcrOwner", $resolvedGhcrOwner) }
if (-not [string]::IsNullOrWhiteSpace($Image)) { $args += @("-Image", $Image) }
if (-not [string]::IsNullOrWhiteSpace($ProviderImage)) { $args += @("-ProviderImage", $ProviderImage) }
if (-not [string]::IsNullOrWhiteSpace($ProviderReleaseTag)) { $args += @("-ProviderReleaseTag", $ProviderReleaseTag) }
if ($SkipPull) { $args += "-SkipPull" }
if (-not [string]::IsNullOrWhiteSpace($RegisterOutputDirHost)) { $args += @("-RegisterOutputDirHost", $RegisterOutputDirHost) }
if (-not [string]::IsNullOrWhiteSpace($ProtocolBridgeVolumeName)) { $args += @("-ProtocolBridgeVolumeName", $ProtocolBridgeVolumeName) }
if (-not [string]::IsNullOrWhiteSpace($RegisterTeamAuthDirHost)) { $args += @("-RegisterTeamAuthDirHost", $RegisterTeamAuthDirHost) }
if (-not [string]::IsNullOrWhiteSpace($RegisterTeamLocalDirHost)) { $args += @("-RegisterTeamLocalDirHost", $RegisterTeamLocalDirHost) }
if (-not [string]::IsNullOrWhiteSpace($MailboxServiceApiKey)) { $args += @("-MailboxServiceApiKey", $MailboxServiceApiKey) }
if (-not [string]::IsNullOrWhiteSpace($EasyProxyApiKey)) { $args += @("-EasyProxyApiKey", $EasyProxyApiKey) }

& powershell @args
