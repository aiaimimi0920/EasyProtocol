param(
    [ValidateSet('python', 'go', 'javascript', 'rust')]
    [string]$Provider,
    [string]$ConfigPath = 'config.yaml',
    [string]$Platform = 'linux/amd64',
    [string]$Image = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')

$config = Read-EasyProtocolConfig -ConfigPath $ConfigPath
$providers = $config.providers
if ($null -eq $providers) {
    throw 'Missing providers section in config.yaml.'
}

$providerConfig = $providers.$Provider
if ($null -eq $providerConfig) {
    throw "Missing providers.$Provider section in config.yaml."
}

function Resolve-EasyBrowserRepoRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $explicitCandidates = @(
        [string]$env:EASYBROWSER_REPO_ROOT
    )
    $defaultCandidates = @(
        (Join-Path (Split-Path -Parent $RepoRoot) 'EasyBrowser'),
        (Join-Path $RepoRoot 'EasyBrowser')
    )

    foreach ($candidate in @($explicitCandidates + $defaultCandidates)) {
        $normalized = [string]$candidate
        if ([string]::IsNullOrWhiteSpace($normalized)) {
            continue
        }
        if (Test-Path -LiteralPath $normalized) {
            return $normalized
        }
    }

    return ''
}

$repoRoot = Get-EasyProtocolRepoRoot
$dockerfileRelative = if ($providerConfig.dockerfile) { [string]$providerConfig.dockerfile } else { "deploy/providers/$Provider/Dockerfile" }
$configuredImage = if ($providerConfig.image) { [string]$providerConfig.image } else { "easyprotocol/${Provider}-protocol-service:local" }
$dockerfilePath = Join-Path $repoRoot $dockerfileRelative
$imageRef = if ([string]::IsNullOrWhiteSpace($Image)) { $configuredImage } else { $Image }

Write-Host "Building provider image [$Provider]: $imageRef" -ForegroundColor Cyan
if ($Provider -eq 'python') {
    $browserRepoRoot = Resolve-EasyBrowserRepoRoot -RepoRoot $repoRoot
    if ([string]::IsNullOrWhiteSpace($browserRepoRoot)) {
        throw "Missing EasyBrowser repo root. Set EASYBROWSER_REPO_ROOT or provide EasyBrowser next to EasyProtocol."
    }
    $browserRuntimeSrc = Join-Path $browserRepoRoot 'runtimes\chrome\src'
    $browserRequirementsPath = Join-Path $browserRepoRoot 'runtimes\chrome\requirements.txt'
    $browserSharedAuthSrc = Join-Path $browserRuntimeSrc 'shared_auth'
    $browserCloudflareClientPath = Join-Path $browserRuntimeSrc 'shared_mailbox\cloudflare_temp_email_client.py'
    if (-not (Test-Path -LiteralPath $browserRuntimeSrc)) {
        throw "Missing EasyBrowser runtime source: $browserRuntimeSrc"
    }
    if (-not (Test-Path -LiteralPath $browserRequirementsPath)) {
        throw "Missing EasyBrowser runtime requirements: $browserRequirementsPath"
    }
    if (-not (Test-Path -LiteralPath $browserSharedAuthSrc)) {
        throw "Missing EasyBrowser shared_auth source: $browserSharedAuthSrc"
    }
    if (-not (Test-Path -LiteralPath $browserCloudflareClientPath)) {
        throw "Missing EasyBrowser cloudflare mailbox client: $browserCloudflareClientPath"
    }

    $tempContext = Join-Path ([System.IO.Path]::GetTempPath()) ("easyprotocol-python-build-" + [guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Force -Path $tempContext | Out-Null
        $tempProvidersRoot = Join-Path $tempContext 'providers\python'
        $tempBrowserRoot = Join-Path $tempContext 'python_browser_service'
        $tempPythonSharedRoot = Join-Path $tempProvidersRoot 'python_shared\src'
        New-Item -ItemType Directory -Force -Path $tempProvidersRoot | Out-Null
        New-Item -ItemType Directory -Force -Path $tempBrowserRoot | Out-Null
        New-Item -ItemType Directory -Force -Path $tempPythonSharedRoot | Out-Null

        Copy-Item -LiteralPath (Join-Path $repoRoot 'providers\python\src') -Destination $tempProvidersRoot -Recurse -Force
        Copy-Item -LiteralPath (Join-Path $repoRoot 'providers\python\python_shared') -Destination $tempProvidersRoot -Recurse -Force
        Copy-Item -LiteralPath $browserRuntimeSrc -Destination $tempBrowserRoot -Recurse -Force
        Copy-Item -LiteralPath $browserSharedAuthSrc -Destination $tempPythonSharedRoot -Recurse -Force
        Copy-Item -LiteralPath $browserCloudflareClientPath -Destination (Join-Path $tempPythonSharedRoot 'shared_mailbox\cloudflare_temp_email_client.py') -Force
        Copy-Item -LiteralPath $browserRequirementsPath -Destination (Join-Path $tempContext 'browser_runtime_requirements.txt') -Force
        Copy-Item -LiteralPath $dockerfilePath -Destination (Join-Path $tempContext 'Dockerfile') -Force

        docker build --platform $Platform -f (Join-Path $tempContext 'Dockerfile') -t $imageRef $tempContext
        if ($LASTEXITCODE -ne 0) {
            throw "Docker build failed with exit code $LASTEXITCODE"
        }
    } finally {
        Remove-Item -LiteralPath $tempContext -Recurse -Force -ErrorAction SilentlyContinue
    }
} else {
    docker build --platform $Platform -f $dockerfilePath -t $imageRef $repoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Docker build failed with exit code $LASTEXITCODE"
    }
}

Write-Host "Built provider image [$Provider]: $imageRef" -ForegroundColor Green
