param(
    [string]$InstanceName = 'dyn01',
    [string]$ConfigPath = 'config.yaml',
    [int]$GatewayHostPort = 29789,
    [int]$PythonManagerHostPort = 29103,
    [string]$InstanceRoot = '',
    [string]$RegisterOutputDirHost = '',
    [string]$RegisterTeamAuthDirHost = '',
    [string]$RegisterTeamLocalDirHost = '',
    [string]$MailboxServiceApiKey = '',
    [string]$GatewayImage = '',
    [string]$ProviderImage = '',
    [string]$ReleaseTag = '',
    [string]$ProviderReleaseTag = '',
    [string]$GhcrOwner = '',
    [switch]$SkipPull,
    [switch]$NoBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')
. (Join-Path $PSScriptRoot 'lib/easyprotocol-ghcr.ps1')
. (Join-Path $PSScriptRoot 'lib/easyprotocol-network.ps1')

function Find-FreeTcpPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        return [int]$listener.LocalEndpoint.Port
    } finally {
        $listener.Stop()
    }
}

function Get-DefaultInstanceRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $gameEditorRoot = Split-Path -Parent $RepoRoot
    return (Join-Path $gameEditorRoot 'linshi\EasyProtocol\instances')
}

function Resolve-PreferredHostPath {
    param(
        [string]$ExplicitPath,
        [string]$ConfiguredPath,
        [string]$DefaultPath
    )

    foreach ($candidate in @($ExplicitPath, $ConfiguredPath, $DefaultPath)) {
        $normalized = [string]$candidate
        if (-not [string]::IsNullOrWhiteSpace($normalized)) {
            return $normalized
        }
    }

    return ''
}

function Get-DefaultRegisterOutputDirHost {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstanceRoot
    )

    return (Join-Path $InstanceRoot 'register-output')
}

function Test-LegacyRegisterOutputPlaceholder {
    param(
        [string]$Path
    )

    $normalized = ([string]$Path).Trim()
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return $false
    }
    $normalized = $normalized.Replace('\', '/').TrimEnd('/').ToLowerInvariant()
    return $normalized -eq 'c:/easyprotocol/register-output'
}

function Find-EasyEmailConfigPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $gameEditorRoot = Split-Path -Parent $RepoRoot
    $candidates = @(
        (Join-Path $gameEditorRoot 'EasyEmail\config.yaml'),
        (Join-Path $gameEditorRoot 'EmailService\deploy\EasyEmail\config.yaml')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return ''
}

function Read-EasyEmailServerApiKey {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $configPath = Find-EasyEmailConfigPath -RepoRoot $RepoRoot
    if ([string]::IsNullOrWhiteSpace($configPath)) {
        return ''
    }

    Assert-EasyProtocolPythonModule -ModuleName 'yaml' -PackageName 'pyyaml'
    $resolvedConfigPath = Resolve-EasyProtocolPath -Path $configPath
    $script = @"
import pathlib
import yaml
payload = yaml.safe_load(pathlib.Path(r'''$resolvedConfigPath''').read_text(encoding='utf-8')) or {}
service_base = payload.get('serviceBase') if isinstance(payload, dict) else {}
runtime = service_base.get('runtime') if isinstance(service_base, dict) else {}
server = runtime.get('server') if isinstance(runtime, dict) else {}
print(str(server.get('apiKey') or ''))
"@
    $apiKey = (& python -c $script)
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read EasyEmail server apiKey from $resolvedConfigPath"
    }
    return [string]$apiKey
}

function Set-EnvFileVariable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    $lines = @()
    if (Test-Path -LiteralPath $Path) {
        $lines = Get-Content -LiteralPath $Path
    }

    $updated = $false
    for ($index = 0; $index -lt $lines.Count; $index += 1) {
        if ($lines[$index] -match ('^' + [regex]::Escape($Name) + '=')) {
            $lines[$index] = "$Name=$Value"
            $updated = $true
            break
        }
    }

    if (-not $updated) {
        $lines += "$Name=$Value"
    }

    Set-Content -LiteralPath $Path -Value $lines -Encoding UTF8
}

function Resolve-ProviderPublishedImageName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Provider,
        [string]$ConfiguredImage
    )

    $configuredName = [string]($ConfiguredImage -replace '^.+/', '' -replace ':.+$', '')
    if (-not [string]::IsNullOrWhiteSpace($configuredName) -and $configuredName -notmatch '^(local|latest)$') {
        switch ($configuredName) {
            'python-protocol-service' { return 'easy-protocol-python-service' }
            default { return $configuredName }
        }
    }

        switch ($Provider.ToLowerInvariant()) {
            'python' { return 'easy-protocol-python' }
            'go' { return 'easy-protocol-go' }
            'javascript' { return 'easy-protocol-javascript' }
            'rust' { return 'easy-protocol-rust' }
            default { return "easy-protocol-$Provider" }
        }
}

$repoRoot = Get-EasyProtocolRepoRoot
$resolvedConfigPath = if ([System.IO.Path]::IsPathRooted($ConfigPath)) { $ConfigPath } else { Join-Path $repoRoot $ConfigPath }
if (-not (Test-Path -LiteralPath $resolvedConfigPath)) {
    throw "Missing config file: $resolvedConfigPath"
}

if ($GatewayHostPort -le 0) {
    $GatewayHostPort = Find-FreeTcpPort
}
if ($PythonManagerHostPort -le 0) {
    $PythonManagerHostPort = Find-FreeTcpPort
}

$config = Read-EasyProtocolConfig -ConfigPath $resolvedConfigPath
$pythonProvider = $config.providers.python
if ($null -eq $pythonProvider) {
    throw 'Missing providers.python section in config.yaml.'
}
$ghcr = if ($config.publishing) { $config.publishing.ghcr } else { $null }
$registry = if ($ghcr -and $ghcr.registry) { [string]$ghcr.registry } else { 'ghcr.io' }
$configuredGatewayImage = if ($config.serviceBase -and $config.serviceBase.image) { [string]$config.serviceBase.image } else { 'easy-protocol/easy-protocol:local' }
$configuredProviderImage = if ($pythonProvider.image) { [string]$pythonProvider.image } else { 'easy-protocol/easy-protocol-python:local' }
$gatewayImageName = [string]($configuredGatewayImage -replace '^.+/', '' -replace ':.+$', '')
if ([string]::IsNullOrWhiteSpace($gatewayImageName)) { $gatewayImageName = 'easy-protocol' }
$providerImageName = Resolve-ProviderPublishedImageName -Provider 'python' -ConfiguredImage $configuredProviderImage
$useGhcrImages = (-not [string]::IsNullOrWhiteSpace($GatewayImage)) -or (-not [string]::IsNullOrWhiteSpace($ProviderImage)) -or (-not [string]::IsNullOrWhiteSpace($ReleaseTag)) -or (-not [string]::IsNullOrWhiteSpace($ProviderReleaseTag))

if ($useGhcrImages) {
    if ([string]::IsNullOrWhiteSpace($GhcrOwner)) {
        $GhcrOwner = if ($ghcr -and $ghcr.owner) { [string]$ghcr.owner } else { '' }
    }
    Assert-EasyProtocolGhcrOwnerReady -Owner $GhcrOwner -SourceDescription 'GHCR owner'

    if ([string]::IsNullOrWhiteSpace($GatewayImage)) {
        if ([string]::IsNullOrWhiteSpace($ReleaseTag)) {
            throw 'GHCR isolated deployment requires -GatewayImage or -ReleaseTag.'
        }
        $GatewayImage = "$registry/$GhcrOwner/${gatewayImageName}:$ReleaseTag"
    }

    if ([string]::IsNullOrWhiteSpace($ProviderImage)) {
        if ([string]::IsNullOrWhiteSpace($ProviderReleaseTag)) {
            throw 'GHCR isolated deployment requires -ProviderImage or -ProviderReleaseTag.'
        }
        $ProviderImage = "$registry/$GhcrOwner/${providerImageName}:$ProviderReleaseTag"
    }
}

$instanceRootBase = if ([string]::IsNullOrWhiteSpace($InstanceRoot)) {
    Get-DefaultInstanceRoot -RepoRoot $repoRoot
} elseif ([System.IO.Path]::IsPathRooted($InstanceRoot)) {
    $InstanceRoot
} else {
    Join-Path $repoRoot $InstanceRoot
}

$instanceRoot = Join-Path $instanceRootBase $InstanceName
$configDir = Join-Path $instanceRoot 'gateway-config'
$dataDir = Join-Path $instanceRoot 'gateway-data'
$envFile = Join-Path $instanceRoot 'python-manager.env'
$gatewayConfigPath = Join-Path $configDir 'config.yaml'

$pythonMounts = $pythonProvider.hostMounts
$configuredRegisterOutputDirHost = [string]$pythonMounts.registerOutputDirHost
$configuredRegisterTeamAuthDirHost = [string]$pythonMounts.registerTeamAuthDirHost
$configuredRegisterTeamLocalDirHost = [string]$pythonMounts.registerTeamLocalDirHost

if ([string]::IsNullOrWhiteSpace($RegisterOutputDirHost) -and (Test-LegacyRegisterOutputPlaceholder -Path $configuredRegisterOutputDirHost)) {
    $configuredRegisterOutputDirHost = ''
}

$registerOutputDirHost = Resolve-PreferredHostPath `
    -ExplicitPath $RegisterOutputDirHost `
    -ConfiguredPath $configuredRegisterOutputDirHost `
    -DefaultPath (Get-DefaultRegisterOutputDirHost -InstanceRoot $instanceRoot)
$registerTeamAuthDirHost = Resolve-PreferredHostPath `
    -ExplicitPath $RegisterTeamAuthDirHost `
    -ConfiguredPath $configuredRegisterTeamAuthDirHost `
    -DefaultPath ''
$registerTeamLocalDirHost = Resolve-PreferredHostPath `
    -ExplicitPath $RegisterTeamLocalDirHost `
    -ConfiguredPath $configuredRegisterTeamLocalDirHost `
    -DefaultPath ''

$resolvedMailboxServiceApiKey = [string]$MailboxServiceApiKey
if ([string]::IsNullOrWhiteSpace($resolvedMailboxServiceApiKey)) {
    $resolvedMailboxServiceApiKey = [string]$env:MAILBOX_SERVICE_API_KEY
}
if ([string]::IsNullOrWhiteSpace($resolvedMailboxServiceApiKey)) {
    $resolvedMailboxServiceApiKey = Read-EasyEmailServerApiKey -RepoRoot $repoRoot
}

foreach ($path in @($registerOutputDirHost, $registerTeamAuthDirHost, $registerTeamLocalDirHost)) {
    if (-not [string]::IsNullOrWhiteSpace($path) -and -not (Test-Path -LiteralPath $path)) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
}

Write-Host 'Rendering config for isolated instance...' -ForegroundColor Cyan
& (Join-Path $PSScriptRoot 'render-derived-configs.ps1') -ConfigPath $resolvedConfigPath -ServiceBase -EasyProtocol
if ($LASTEXITCODE -ne 0) {
    throw "render-derived-configs.ps1 failed with exit code $LASTEXITCODE"
}

if (-not $NoBuild -and -not $useGhcrImages) {
    & (Join-Path $PSScriptRoot 'compile-service-base-image.ps1') -ConfigPath $resolvedConfigPath
    & (Join-Path $PSScriptRoot 'compile-provider-image.ps1') -Provider python -ConfigPath $resolvedConfigPath
}

if ($useGhcrImages -and -not $SkipPull) {
    docker pull $GatewayImage | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to pull isolated gateway image: $GatewayImage"
    }
    docker pull $ProviderImage | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to pull isolated provider image: $ProviderImage"
    }
}

Ensure-EasyProtocolExternalNetwork -NetworkName 'EasyAiMi'

New-Item -ItemType Directory -Force -Path $configDir | Out-Null
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$managerAlias = "easy-protocol-python-$InstanceName"
$managerContainerName = "easy-protocol-python-$InstanceName"
$gatewayContainerName = "easy-protocol-$InstanceName"

$renderedGatewayConfigPath = Join-Path $repoRoot 'deploy/service/base/config/config.yaml'
$gatewayConfigText = Get-Content -Raw -LiteralPath $renderedGatewayConfigPath
$gatewayConfigText = $gatewayConfigText -replace 'http://python-protocol-manager:9100', "http://$managerAlias`:9100"
$gatewayConfigText = $gatewayConfigText -replace 'http://easy-protocol-python:9100', "http://$managerAlias`:9100"
Set-Content -LiteralPath $gatewayConfigPath -Value $gatewayConfigText -Encoding UTF8

$renderedEnvPath = Join-Path $repoRoot 'deploy/stacks/easy-protocol/generated/stack.env'
Copy-Item -LiteralPath $renderedEnvPath -Destination $envFile -Force
Set-EnvFileVariable -Path $envFile -Name 'REGISTER_OUTPUT_DIR_HOST' -Value $registerOutputDirHost
Set-EnvFileVariable -Path $envFile -Name 'REGISTER_TEAM_AUTH_DIR_HOST' -Value $registerTeamAuthDirHost
Set-EnvFileVariable -Path $envFile -Name 'REGISTER_TEAM_LOCAL_DIR_HOST' -Value $registerTeamLocalDirHost
if (-not [string]::IsNullOrWhiteSpace($resolvedMailboxServiceApiKey)) {
    Set-EnvFileVariable -Path $envFile -Name 'MAILBOX_SERVICE_API_KEY' -Value $resolvedMailboxServiceApiKey
}

$existingContainers = @(docker ps -a --format '{{.Names}}')
if ($LASTEXITCODE -ne 0) {
    throw "docker ps -a failed with exit code $LASTEXITCODE"
}
if ($existingContainers -contains $managerContainerName) {
    docker rm -f $managerContainerName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to remove existing container: $managerContainerName"
    }
}
if ($existingContainers -contains $gatewayContainerName) {
    docker rm -f $gatewayContainerName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to remove existing container: $gatewayContainerName"
    }
}

docker run -d `
    --name $managerContainerName `
    --network EasyAiMi `
    --network-alias $managerAlias `
    --network-alias $managerContainerName `
    -p "${PythonManagerHostPort}:9100" `
    --env-file $envFile `
    -v "${registerOutputDirHost}:/shared/register-output" `
    -v "${registerTeamAuthDirHost}:/shared/team-auth:ro" `
    -v "${registerTeamLocalDirHost}:/shared/local-team-store" `
    $(if ($useGhcrImages) { $ProviderImage } else { 'easy-protocol/easy-protocol-python:local' }) | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start isolated python manager container"
}

docker run -d `
    --name $gatewayContainerName `
    --network EasyAiMi `
    --network-alias "easy-protocol-$InstanceName" `
    --network-alias $gatewayContainerName `
    -p "${GatewayHostPort}:9788" `
    -e EASY_PROTOCOL_CONFIG_PATH=/etc/easy-protocol/config.yaml `
    -e EASY_PROTOCOL_STATE_DIR=/var/lib/easy-protocol `
    -e EASY_PROTOCOL_RESET_STORE_ON_BOOT=false `
    -v "${configDir}:/etc/easy-protocol" `
    -v "${dataDir}:/var/lib/easy-protocol" `
    $(if ($useGhcrImages) { $GatewayImage } else { 'easy-protocol/easy-protocol:local' }) | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start isolated easyprotocol gateway container"
}

$managerBaseUrl = "http://127.0.0.1:${PythonManagerHostPort}"
$gatewayBaseUrl = "http://127.0.0.1:${GatewayHostPort}"
$managerHealth = $null
$gatewayHealth = $null
$lastError = ''

for ($attempt = 1; $attempt -le 30; $attempt += 1) {
    try {
        $managerHealth = Invoke-RestMethod -Uri ($managerBaseUrl + '/health') -Method Get -TimeoutSec 10
        $gatewayHealth = Invoke-RestMethod -Uri ($gatewayBaseUrl + '/api/health') -Method Get -TimeoutSec 10
        break
    } catch {
        $lastError = $_.Exception.Message
        Start-Sleep -Seconds 1
    }
}

if ($null -eq $managerHealth -or $null -eq $gatewayHealth) {
    throw "Isolated instance failed health checks: $lastError"
}

$gatewayRequest = @{
    request_id = "isolated-$InstanceName-smoke"
    operation  = 'codex.semantic.step'
    payload    = @{
        step_type  = 'worker_runtime_probe'
        step_input = @{
            label = "isolated-$InstanceName"
        }
    }
} | ConvertTo-Json -Depth 10

$gatewayInvoke = Invoke-RestMethod -Uri ($gatewayBaseUrl + '/api/public/request') -Method Post -Body $gatewayRequest -ContentType 'application/json' -TimeoutSec 30

if ([string]$gatewayInvoke.status -ne 'succeeded') {
    throw 'Isolated gateway invoke smoke did not return a success response.'
}

$managerPool = $null
if ($managerHealth -and $managerHealth.PSObject.Properties.Match('pool').Count -gt 0) {
    $managerPool = $managerHealth.pool
}
if ($null -eq $managerPool) {
    throw 'Isolated python manager /health response is missing pool status.'
}

[pscustomobject]@{
    instanceName          = $InstanceName
    network               = 'EasyAiMi'
    registerOutputDirHost = $registerOutputDirHost
    registerTeamAuthDirHost = $registerTeamAuthDirHost
    registerTeamLocalDirHost = $registerTeamLocalDirHost
    managerAlias          = $managerAlias
    managerContainerName  = $managerContainerName
    managerBaseUrl        = $managerBaseUrl
    gatewayContainerName  = $gatewayContainerName
    gatewayBaseUrl        = $gatewayBaseUrl
    managerPool           = $managerPool
    gatewayHealthStatus   = $gatewayHealth.status
    gatewayInvokeStatus   = $gatewayInvoke.status
    gatewayInvokeResult   = $gatewayInvoke.result
} | ConvertTo-Json -Depth 20
