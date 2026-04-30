param(
    [string]$InstanceName = 'dyn01',
    [string]$ConfigPath = 'config.yaml',
    [int]$GatewayHostPort = 29789,
    [int]$PythonManagerHostPort = 29103,
    [switch]$NoBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')
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

$pythonMounts = $pythonProvider.hostMounts
$registerOutputDirHost = [string]$pythonMounts.registerOutputDirHost
$registerTeamAuthDirHost = [string]$pythonMounts.registerTeamAuthDirHost
$registerTeamLocalDirHost = [string]$pythonMounts.registerTeamLocalDirHost

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

if (-not $NoBuild) {
    & (Join-Path $PSScriptRoot 'compile-service-base-image.ps1') -ConfigPath $resolvedConfigPath
    & (Join-Path $PSScriptRoot 'compile-provider-image.ps1') -Provider python -ConfigPath $resolvedConfigPath
}

Ensure-EasyProtocolExternalNetwork -NetworkName 'EasyAiMi'

$instanceRoot = Join-Path $repoRoot ".tmp\\instances\\$InstanceName"
$configDir = Join-Path $instanceRoot 'gateway-config'
$dataDir = Join-Path $instanceRoot 'gateway-data'
$envFile = Join-Path $instanceRoot 'python-manager.env'
$gatewayConfigPath = Join-Path $configDir 'config.yaml'

New-Item -ItemType Directory -Force -Path $configDir | Out-Null
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$managerAlias = "python-protocol-manager-$InstanceName"
$managerContainerName = "easyprotocol-python-manager-$InstanceName"
$gatewayContainerName = "easyprotocol-service-$InstanceName"

$renderedGatewayConfigPath = Join-Path $repoRoot 'deploy/service/base/config/config.yaml'
$gatewayConfigText = Get-Content -Raw -LiteralPath $renderedGatewayConfigPath
$gatewayConfigText = $gatewayConfigText -replace 'http://python-protocol-manager:9100', "http://$managerAlias`:9100"
Set-Content -LiteralPath $gatewayConfigPath -Value $gatewayConfigText -Encoding UTF8

$renderedEnvPath = Join-Path $repoRoot 'deploy/stacks/easy-protocol/generated/stack.env'
Copy-Item -LiteralPath $renderedEnvPath -Destination $envFile -Force

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
    -p "${PythonManagerHostPort}:9100" `
    --env-file $envFile `
    -v "${registerOutputDirHost}:/shared/register-output" `
    -v "${registerTeamAuthDirHost}:/shared/team-auth:ro" `
    -v "${registerTeamLocalDirHost}:/shared/local-team-store" `
    easyprotocol/python-protocol-service:local | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start isolated python manager container"
}

docker run -d `
    --name $gatewayContainerName `
    --network EasyAiMi `
    --network-alias "easy-protocol-service-$InstanceName" `
    -p "${GatewayHostPort}:9788" `
    -e EASY_PROTOCOL_CONFIG_PATH=/etc/easy-protocol/config.yaml `
    -e EASY_PROTOCOL_STATE_DIR=/var/lib/easy-protocol `
    -e EASY_PROTOCOL_RESET_STORE_ON_BOOT=false `
    -v "${configDir}:/etc/easy-protocol" `
    -v "${dataDir}:/var/lib/easy-protocol" `
    easyprotocol/easy-protocol-service:local | Out-Null
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
    managerContainerName  = $managerContainerName
    managerBaseUrl        = $managerBaseUrl
    gatewayContainerName  = $gatewayContainerName
    gatewayBaseUrl        = $gatewayBaseUrl
    managerPool           = $managerPool
    gatewayHealthStatus   = $gatewayHealth.status
    gatewayInvokeStatus   = $gatewayInvoke.status
    gatewayInvokeResult   = $gatewayInvoke.result
} | ConvertTo-Json -Depth 20
