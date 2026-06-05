param(
    [string]$EasyBrowserRepoRoot = '',
    [string]$DestinationRoot = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')

function Resolve-EasyBrowserRepoRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [string]$ExplicitRoot
    )

    $candidates = @(
        $ExplicitRoot,
        [string]$env:EASYBROWSER_REPO_ROOT,
        (Join-Path (Split-Path -Parent $RepoRoot) 'EasyBrowser'),
        (Join-Path $RepoRoot 'EasyBrowser')
    )

    foreach ($candidate in $candidates) {
        $normalized = [string]$candidate
        if ([string]::IsNullOrWhiteSpace($normalized)) {
            continue
        }
        if (Test-Path -LiteralPath $normalized) {
            return (Resolve-Path -LiteralPath $normalized).Path
        }
    }

    return ''
}

$repoRoot = Get-EasyProtocolRepoRoot
$destination = if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
    $repoRoot
} else {
    $DestinationRoot
}

$browserRoot = Resolve-EasyBrowserRepoRoot -RepoRoot $repoRoot -ExplicitRoot $EasyBrowserRepoRoot
if ([string]::IsNullOrWhiteSpace($browserRoot)) {
    throw 'Missing EasyBrowser repo root. Set -EasyBrowserRepoRoot, EASYBROWSER_REPO_ROOT, or provide EasyBrowser next to EasyProtocol.'
}

$browserRuntimeSrc = Join-Path $browserRoot 'runtimes\chrome\src'
$browserRequirementsPath = Join-Path $browserRoot 'runtimes\chrome\requirements.txt'
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

$destinationBrowserRoot = Join-Path $destination 'python_browser_service'
$destinationBrowserSrc = Join-Path $destinationBrowserRoot 'src'
$destinationRequirementsPath = Join-Path $destination 'browser_runtime_requirements.txt'

if (Test-Path -LiteralPath $destinationBrowserRoot) {
    Remove-Item -LiteralPath $destinationBrowserRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $destinationBrowserRoot | Out-Null

Copy-Item -LiteralPath $browserRuntimeSrc -Destination $destinationBrowserRoot -Recurse -Force
Copy-Item -LiteralPath $browserRequirementsPath -Destination $destinationRequirementsPath -Force

$pythonSharedRoot = Join-Path $destination 'providers\python\python_shared\src'
if (Test-Path -LiteralPath $pythonSharedRoot) {
    $destinationSharedAuth = Join-Path $pythonSharedRoot 'shared_auth'
    $destinationSharedMailbox = Join-Path $pythonSharedRoot 'shared_mailbox'

    if (Test-Path -LiteralPath $destinationSharedAuth) {
        Remove-Item -LiteralPath $destinationSharedAuth -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $destinationSharedMailbox | Out-Null

    Copy-Item -LiteralPath $browserSharedAuthSrc -Destination $pythonSharedRoot -Recurse -Force
    Copy-Item -LiteralPath $browserCloudflareClientPath -Destination (Join-Path $destinationSharedMailbox 'cloudflare_temp_email_client.py') -Force
}

Write-Host "Materialized EasyBrowser runtime from $browserRoot into $destinationBrowserSrc" -ForegroundColor Green
