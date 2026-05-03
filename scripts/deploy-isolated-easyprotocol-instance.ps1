param(
    [string]$InstanceName = 'dyn01',
    [string]$ConfigPath = 'config.yaml',
    [int]$GatewayHostPort = 29789,
    [int]$PythonManagerHostPort = 29103,
    [int]$PythonSlot = 1,
    [string]$InstanceRoot = '',
    [string]$RegisterOutputDirHost = '',
    [string]$RegisterTeamAuthDirHost = '',
    [string]$RegisterTeamLocalDirHost = '',
    [string]$MailboxServiceApiKey = '',
    [string]$EasyProxyApiKey = '',
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

function Find-EasyProxyConfigPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $gameEditorRoot = Split-Path -Parent $RepoRoot
    $candidates = @(
        (Join-Path $gameEditorRoot 'EasyProxy\config.yaml'),
        (Join-Path $gameEditorRoot 'ProxyService\deploy\EasyProxy\config.yaml')
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

function Read-EasyProxyManagementApiKey {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $configPath = Find-EasyProxyConfigPath -RepoRoot $RepoRoot
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
management = runtime.get('management') if isinstance(runtime, dict) else {}
print(str(management.get('password') or ''))
"@
    $apiKey = (& python -c $script)
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read EasyProxy management api key from $resolvedConfigPath"
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

function Update-ManagedProviderRuntimePythonConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConfigPath,
        [Parameter(Mandatory = $true)]
        [string]$RegisterOutputDirHost,
        [Parameter(Mandatory = $true)]
        [string]$RegisterTeamAuthDirHost,
        [Parameter(Mandatory = $true)]
        [string]$RegisterTeamLocalDirHost,
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$MailboxServiceApiKey,
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$EasyProxyApiKey
    )

    Assert-EasyProtocolPythonModule -ModuleName 'yaml' -PackageName 'pyyaml'
    $resolvedConfigPath = Resolve-EasyProtocolPath -Path $ConfigPath
    $script = @"
import pathlib
import yaml

config_path = pathlib.Path(r'''$resolvedConfigPath''')
payload = yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
managed = payload.setdefault('managed_provider_runtime', {})
providers = managed.setdefault('providers', {})
python_cfg = providers.setdefault('python', {})
env_map = python_cfg.setdefault('environment', {})
if r'''$MailboxServiceApiKey''':
    env_map['MAILBOX_SERVICE_API_KEY'] = r'''$MailboxServiceApiKey'''
if r'''$EasyProxyApiKey''':
    env_map['EASY_PROXY_API_KEY'] = r'''$EasyProxyApiKey'''
python_cfg['host_mounts'] = [
    {'source': r'''$RegisterOutputDirHost''', 'target': '/shared/register-output', 'read_only': False},
    {'source': r'''$RegisterTeamAuthDirHost''', 'target': '/shared/team-auth', 'read_only': True},
    {'source': r'''$RegisterTeamLocalDirHost''', 'target': '/shared/local-team-store', 'read_only': False},
]
config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding='utf-8')
"@
    & python -c $script
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to update managed provider runtime python config in $resolvedConfigPath"
    }
}

function Update-ManagedProviderRuntimeImages {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConfigPath,
        [Parameter(Mandatory = $true)]
        [array]$ProviderRuntimePlans
    )

    Assert-EasyProtocolPythonModule -ModuleName 'yaml' -PackageName 'pyyaml'
    $resolvedConfigPath = Resolve-EasyProtocolPath -Path $ConfigPath
    $plansJson = $ProviderRuntimePlans | ConvertTo-Json -Depth 10 -Compress
    $plansJsonBase64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($plansJson))
    $script = @"
import base64
import json
import pathlib
import yaml

config_path = pathlib.Path(r'''$resolvedConfigPath''')
payload = yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
managed = payload.setdefault('managed_provider_runtime', {})
providers = managed.setdefault('providers', {})
plans_json = base64.b64decode(r'''$plansJsonBase64''').decode('utf-8')
loaded = json.loads(plans_json)
if isinstance(loaded, dict):
    if 'providerKey' in loaded:
        loaded = [loaded]
    else:
        loaded = list(loaded.values())
for item in loaded:
    provider_key = str(item.get('providerKey') or '').strip()
    image = str(item.get('image') or '').strip()
    if not provider_key or not image:
        continue
    provider = providers.setdefault(provider_key, {})
    provider['image'] = image
config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding='utf-8')
"@
    & python -c $script
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to update managed provider runtime images in $resolvedConfigPath"
    }
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

function Format-ReplicaSuffix {
    param(
        [int]$Index
    )

    $safeIndex = [Math]::Max(1, [int]$Index)
    return ('{0:000}' -f $safeIndex)
}

function Get-DefaultProviderEndpointHostPrefix {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Provider
    )

    switch ($Provider.ToLowerInvariant()) {
        'python' { return 'easy-protocol-python' }
        'go' { return 'easy-protocol-go' }
        'javascript' { return 'easy-protocol-javascript' }
        'rust' { return 'easy-protocol-rust' }
        default { return "easy-protocol-$Provider" }
    }
}

function Get-ProviderLocalImageName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Provider
    )

    switch ($Provider.ToLowerInvariant()) {
        'python' { return 'easy-protocol/easy-protocol-python:local' }
        'go' { return 'easy-protocol/easy-protocol-go:local' }
        'javascript' { return 'easy-protocol/easy-protocol-javascript:local' }
        'rust' { return 'easy-protocol/easy-protocol-rust:local' }
        default { return "easy-protocol/easy-protocol-$Provider:local" }
    }
}

function Resolve-ProviderRuntimeImage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Provider,
        [Parameter(Mandatory = $true)]
        $ProviderConfig,
        [bool]$UseGhcrImages,
        [string]$Registry,
        [string]$GhcrOwner,
        [string]$ProviderReleaseTag,
        [string]$PythonProviderImageOverride
    )

    if ($Provider -eq 'python' -and -not [string]::IsNullOrWhiteSpace($PythonProviderImageOverride)) {
        return $PythonProviderImageOverride
    }

    $configuredImage = if ($ProviderConfig.image) {
        [string]$ProviderConfig.image
    } else {
        Get-ProviderLocalImageName -Provider $Provider
    }

    if (-not $UseGhcrImages) {
        return $configuredImage
    }

    $imageName = Resolve-ProviderPublishedImageName -Provider $Provider -ConfiguredImage $configuredImage
    return "$Registry/$GhcrOwner/${imageName}:$ProviderReleaseTag"
}

function Get-ProviderEnvMap {
    param(
        [Parameter(Mandatory = $true)]
        $ProviderConfig
    )

    $result = [ordered]@{}
    $containerEnvironment = $ProviderConfig.containerEnvironment
    if ($null -eq $containerEnvironment) {
        return $result
    }

    foreach ($property in $containerEnvironment.PSObject.Properties) {
        $result[[string]$property.Name] = [string]$property.Value
    }

    return $result
}

function Write-ProviderEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [hashtable]$EnvMap
    )

    $lines = @()
    foreach ($key in $EnvMap.Keys) {
        $lines += "$key=$($EnvMap[$key])"
    }
    Set-Content -LiteralPath $Path -Value $lines -Encoding UTF8
}

function ConvertTo-ComposePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return ($Path -replace '\\', '/')
}

function ConvertTo-YamlSingleQuoted {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    return "'" + ($Value -replace "'", "''") + "'"
}

function Write-IsolatedComposeFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$GatewayContainerName,
        [Parameter(Mandatory = $true)]
        [string]$GatewayImage,
        [Parameter(Mandatory = $true)]
        [string]$ConfigDir,
        [Parameter(Mandatory = $true)]
        [string]$DataDir,
        [Parameter(Mandatory = $true)]
        [int]$GatewayHostPort,
        [Parameter(Mandatory = $true)]
        [array]$ProviderRuntimePlans,
        [Parameter(Mandatory = $true)]
        [hashtable]$ProviderEnvFiles,
        [Parameter(Mandatory = $true)]
        [string]$RegisterOutputDirHost,
        [Parameter(Mandatory = $true)]
        [string]$RegisterTeamAuthDirHost,
        [Parameter(Mandatory = $true)]
        [string]$RegisterTeamLocalDirHost,
        [Parameter(Mandatory = $true)]
        [string]$SelectedPythonContainerName,
        [Parameter(Mandatory = $true)]
        [int]$PythonManagerHostPort
    )

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('services:')
    foreach ($providerPlan in $ProviderRuntimePlans) {
        $serviceName = [string]$providerPlan.containerName
        $lines.Add("  ${serviceName}:")
        $lines.Add("    image: " + (ConvertTo-YamlSingleQuoted -Value ([string]$providerPlan.image)))
        $lines.Add("    container_name: " + (ConvertTo-YamlSingleQuoted -Value $serviceName))
        $lines.Add('    restart: unless-stopped')
        $envFilePath = [string]$ProviderEnvFiles[$serviceName]
        if (-not [string]::IsNullOrWhiteSpace($envFilePath)) {
            $lines.Add('    env_file:')
            $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value (ConvertTo-ComposePath -Path $envFilePath)))
        }
        if ($providerPlan.providerKey -eq 'python') {
            $lines.Add('    volumes:')
            $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value ((ConvertTo-ComposePath -Path $RegisterOutputDirHost) + ':/shared/register-output')))
            $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value ((ConvertTo-ComposePath -Path $RegisterTeamAuthDirHost) + ':/shared/team-auth:ro')))
            $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value ((ConvertTo-ComposePath -Path $RegisterTeamLocalDirHost) + ':/shared/local-team-store')))
        }
        if ($serviceName -eq $SelectedPythonContainerName) {
            $lines.Add('    ports:')
            $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value ("${PythonManagerHostPort}:9100")))
        }
        $lines.Add('    networks:')
        $lines.Add('      easy_network:')
        $lines.Add('        aliases:')
        $lines.Add("          - " + (ConvertTo-YamlSingleQuoted -Value ([string]$providerPlan.endpointAlias)))
        if ([string]$providerPlan.endpointAlias -ne $serviceName) {
            $lines.Add("          - " + (ConvertTo-YamlSingleQuoted -Value $serviceName))
        }
    }

    $lines.Add("  ${GatewayContainerName}:")
    $lines.Add("    image: " + (ConvertTo-YamlSingleQuoted -Value $GatewayImage))
    $lines.Add("    container_name: " + (ConvertTo-YamlSingleQuoted -Value $GatewayContainerName))
    $lines.Add('    restart: unless-stopped')
    if ($ProviderRuntimePlans.Count -gt 0) {
        $lines.Add('    depends_on:')
        foreach ($providerPlan in $ProviderRuntimePlans) {
            $lines.Add("      - " + ([string]$providerPlan.containerName))
        }
    }
    $lines.Add('    ports:')
    $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value ("${GatewayHostPort}:9788")))
    $lines.Add('    environment:')
    $lines.Add("      EASY_PROTOCOL_CONFIG_PATH: " + (ConvertTo-YamlSingleQuoted -Value '/etc/easy-protocol/config.yaml'))
    $lines.Add("      EASY_PROTOCOL_STATE_DIR: " + (ConvertTo-YamlSingleQuoted -Value '/var/lib/easy-protocol'))
    $lines.Add("      EASY_PROTOCOL_RESET_STORE_ON_BOOT: " + (ConvertTo-YamlSingleQuoted -Value 'false'))
    $lines.Add('    volumes:')
    $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value ((ConvertTo-ComposePath -Path $ConfigDir) + ':/etc/easy-protocol')))
    $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value ((ConvertTo-ComposePath -Path $DataDir) + ':/var/lib/easy-protocol')))
    $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value ((ConvertTo-ComposePath -Path $RegisterOutputDirHost) + ':/shared/register-output')))
    $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value ((ConvertTo-ComposePath -Path $RegisterTeamAuthDirHost) + ':/shared/team-auth:ro')))
    $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value ((ConvertTo-ComposePath -Path $RegisterTeamLocalDirHost) + ':/shared/local-team-store')))
    $lines.Add("      - " + (ConvertTo-YamlSingleQuoted -Value '/var/run/docker.sock:/var/run/docker.sock'))
    $lines.Add('    networks:')
    $lines.Add('      easy_network:')
    $lines.Add('        aliases:')
    $lines.Add("          - " + (ConvertTo-YamlSingleQuoted -Value $GatewayContainerName))

    $lines.Add('networks:')
    $lines.Add('  easy_network:')
    $lines.Add("    name: " + (ConvertTo-YamlSingleQuoted -Value 'EasyAiMi'))
    $lines.Add('    external: true')

    Set-Content -LiteralPath $Path -Value $lines -Encoding UTF8
}

function Get-EnabledProviderRuntimePlans {
    param(
        [Parameter(Mandatory = $true)]
        $ServiceBaseConfig,
        [Parameter(Mandatory = $true)]
        $ProvidersConfig,
        [bool]$UseGhcrImages,
        [string]$Registry,
        [string]$GhcrOwner,
        [string]$ProviderReleaseTag,
        [string]$PythonProviderImageOverride
    )

    $plans = @()
    $runtimeConfig = if ($null -ne $ServiceBaseConfig) { $ServiceBaseConfig.runtime } else { $null }
    $providerPoolConfig = $null
    if ($null -ne $runtimeConfig) {
        if ($runtimeConfig.PSObject.Properties.Match('providerPool').Count -gt 0) {
            $providerPoolConfig = $runtimeConfig.providerPool
        } elseif ($runtimeConfig.PSObject.Properties.Match('provider_pool').Count -gt 0) {
            $providerPoolConfig = $runtimeConfig.provider_pool
        }
    }
    $poolProviders = if ($null -ne $providerPoolConfig) { $providerPoolConfig.providers } else { $null }

    foreach ($providerProperty in $ProvidersConfig.PSObject.Properties) {
        $providerKey = [string]$providerProperty.Name
        $providerConfig = $providerProperty.Value
        if ($null -eq $providerConfig) {
            continue
        }

        $registryConfig = $providerConfig.registry
        if ($null -eq $registryConfig) {
            continue
        }

        $enabled = $true
        if ($registryConfig.PSObject.Properties.Match('enabled').Count -gt 0) {
            $enabled = [bool]$registryConfig.enabled
        }
        if (-not $enabled) {
            continue
        }

        $poolProviderConfig = if ($null -ne $poolProviders) { $poolProviders.$providerKey } else { $null }
        $warmReplicasRaw = $null
        if ($null -ne $poolProviderConfig -and $poolProviderConfig.PSObject.Properties.Match('warmReplicas').Count -gt 0) {
            $warmReplicasRaw = $poolProviderConfig.warmReplicas
        }

        $replicaCount = 1
        if ($null -ne $warmReplicasRaw) {
            try {
                $replicaCount = [int]$warmReplicasRaw
            } catch {
                $replicaCount = 1
            }
        } elseif ($registryConfig.PSObject.Properties.Match('replicas').Count -gt 0) {
            try {
                $replicaCount = [int]$registryConfig.replicas
            } catch {
                $replicaCount = 1
            }
        }
        if ($replicaCount -lt 1) {
            $replicaCount = 1
        }

        $port = 9100
        if ($registryConfig.PSObject.Properties.Match('port').Count -gt 0) {
            try {
                $port = [int]$registryConfig.port
            } catch {
                $port = 9100
            }
        }

        $endpointHostPrefix = ''
        if ($registryConfig.PSObject.Properties.Match('endpointHostPrefix').Count -gt 0) {
            $endpointHostPrefix = [string]$registryConfig.endpointHostPrefix
        }
        if ([string]::IsNullOrWhiteSpace($endpointHostPrefix)) {
            $endpointHostPrefix = Get-DefaultProviderEndpointHostPrefix -Provider $providerKey
        }

        $image = Resolve-ProviderRuntimeImage `
            -Provider $providerKey `
            -ProviderConfig $providerConfig `
            -UseGhcrImages $UseGhcrImages `
            -Registry $Registry `
            -GhcrOwner $GhcrOwner `
            -ProviderReleaseTag $ProviderReleaseTag `
            -PythonProviderImageOverride $PythonProviderImageOverride

        $envMap = Get-ProviderEnvMap -ProviderConfig $providerConfig

        for ($index = 1; $index -le $replicaCount; $index += 1) {
            $suffix = Format-ReplicaSuffix -Index $index
            $containerName = "$endpointHostPrefix-$suffix"
            $plans += [pscustomobject]@{
                providerKey    = $providerKey
                replicaIndex   = $index
                replicaSuffix  = $suffix
                endpointAlias  = $containerName
                containerName  = $containerName
                image          = $image
                port           = $port
                envMap         = $envMap
            }
        }
    }

    return $plans
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
$providers = $config.providers
$pythonRegistry = $pythonProvider.registry
$pythonReplicaCount = 1
if ($null -ne $pythonRegistry -and $pythonRegistry.PSObject.Properties.Match('replicas').Count -gt 0) {
    try {
        $pythonReplicaCount = [int]$pythonRegistry.replicas
    } catch {
        $pythonReplicaCount = 1
    }
}
if ($pythonReplicaCount -lt 1) {
    $pythonReplicaCount = 1
}
if ($PythonSlot -gt $pythonReplicaCount) {
    throw "PythonSlot=$PythonSlot exceeds configured providers.python.registry.replicas=$pythonReplicaCount. Increase replicas first."
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
            if (-not [string]::IsNullOrWhiteSpace($ReleaseTag)) {
                $ProviderReleaseTag = $ReleaseTag
            } else {
                throw 'GHCR isolated deployment requires -ProviderImage or -ProviderReleaseTag.'
            }
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

$resolvedEasyProxyApiKey = [string]$EasyProxyApiKey
if ([string]::IsNullOrWhiteSpace($resolvedEasyProxyApiKey)) {
    $resolvedEasyProxyApiKey = [string]$env:EASY_PROXY_API_KEY
}
if ([string]::IsNullOrWhiteSpace($resolvedEasyProxyApiKey)) {
    $resolvedEasyProxyApiKey = Read-EasyProxyManagementApiKey -RepoRoot $repoRoot
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
}

$providerRuntimePlans = @(Get-EnabledProviderRuntimePlans `
    -ServiceBaseConfig $config.serviceBase `
    -ProvidersConfig $providers `
    -UseGhcrImages $useGhcrImages `
    -Registry $registry `
    -GhcrOwner $GhcrOwner `
    -ProviderReleaseTag $ProviderReleaseTag `
    -PythonProviderImageOverride $ProviderImage)

if ($providerRuntimePlans.Count -eq 0) {
    throw 'No enabled provider runtime plans were generated from config.yaml.'
}

if ($useGhcrImages -and -not $SkipPull) {
    foreach ($imageRef in ($providerRuntimePlans | ForEach-Object { $_.image } | Sort-Object -Unique)) {
        docker pull $imageRef | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to pull isolated provider image: $imageRef"
        }
    }
}

Ensure-EasyProtocolExternalNetwork -NetworkName 'EasyAiMi'

New-Item -ItemType Directory -Force -Path $configDir | Out-Null
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$pythonReplicaSuffix = Format-ReplicaSuffix -Index $PythonSlot
$pythonManagerPlan = $providerRuntimePlans | Where-Object {
    $_.providerKey -eq 'python' -and $_.replicaSuffix -eq $pythonReplicaSuffix
} | Select-Object -First 1
if ($null -eq $pythonManagerPlan) {
    throw "No python provider runtime plan matched PythonSlot=$PythonSlot"
}
$managerAlias = [string]$pythonManagerPlan.endpointAlias
$managerContainerName = [string]$pythonManagerPlan.containerName
$gatewayContainerName = "easy-protocol"

$renderedGatewayConfigPath = Join-Path $repoRoot 'deploy/service/base/config/config.yaml'
$gatewayConfigText = Get-Content -Raw -LiteralPath $renderedGatewayConfigPath
$gatewayConfigText = $gatewayConfigText -replace 'http://python-protocol-manager:9100', "http://$managerAlias`:9100"
$gatewayConfigText = $gatewayConfigText -replace 'http://easy-protocol-python:9100', "http://$managerAlias`:9100"
Set-Content -LiteralPath $gatewayConfigPath -Value $gatewayConfigText -Encoding UTF8
Update-ManagedProviderRuntimeImages -ConfigPath $gatewayConfigPath -ProviderRuntimePlans $providerRuntimePlans
Update-ManagedProviderRuntimePythonConfig `
    -ConfigPath $gatewayConfigPath `
    -RegisterOutputDirHost $registerOutputDirHost `
    -RegisterTeamAuthDirHost $registerTeamAuthDirHost `
    -RegisterTeamLocalDirHost $registerTeamLocalDirHost `
    -MailboxServiceApiKey $resolvedMailboxServiceApiKey `
    -EasyProxyApiKey $resolvedEasyProxyApiKey

$renderedEnvPath = Join-Path $repoRoot 'deploy/stacks/easy-protocol/generated/stack.env'
Copy-Item -LiteralPath $renderedEnvPath -Destination $envFile -Force
Set-EnvFileVariable -Path $envFile -Name 'REGISTER_OUTPUT_DIR_HOST' -Value $registerOutputDirHost
Set-EnvFileVariable -Path $envFile -Name 'REGISTER_TEAM_AUTH_DIR_HOST' -Value $registerTeamAuthDirHost
Set-EnvFileVariable -Path $envFile -Name 'REGISTER_TEAM_LOCAL_DIR_HOST' -Value $registerTeamLocalDirHost
if (-not [string]::IsNullOrWhiteSpace($resolvedMailboxServiceApiKey)) {
    Set-EnvFileVariable -Path $envFile -Name 'MAILBOX_SERVICE_API_KEY' -Value $resolvedMailboxServiceApiKey
}
if (-not [string]::IsNullOrWhiteSpace($resolvedEasyProxyApiKey)) {
    Set-EnvFileVariable -Path $envFile -Name 'EASY_PROXY_API_KEY' -Value $resolvedEasyProxyApiKey
}

$projectContainers = @(docker ps -a --filter 'label=com.docker.compose.project=easy-protocol' --format '{{.Names}}')
if ($LASTEXITCODE -ne 0) {
    throw "docker ps -a failed while listing compose project containers with exit code $LASTEXITCODE"
}
foreach ($containerName in ($projectContainers | Sort-Object -Unique)) {
    if ([string]::IsNullOrWhiteSpace($containerName)) {
        continue
    }
    docker rm -f $containerName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to remove existing project container: $containerName"
    }
}

$providerEnvFiles = @{}
foreach ($providerPlan in $providerRuntimePlans) {
    $providerEnvFile = if ($providerPlan.providerKey -eq 'python' -and $providerPlan.replicaSuffix -eq $pythonReplicaSuffix) {
        $envFile
    } else {
        Join-Path $instanceRoot ("$($providerPlan.containerName).env")
    }

    if (-not ($providerPlan.providerKey -eq 'python' -and $providerPlan.replicaSuffix -eq $pythonReplicaSuffix)) {
        Write-ProviderEnvFile -Path $providerEnvFile -EnvMap $providerPlan.envMap
    }
    $providerEnvFiles[[string]$providerPlan.containerName] = $providerEnvFile
}

$composeFile = Join-Path $instanceRoot 'docker-compose.generated.yaml'
Write-IsolatedComposeFile `
    -Path $composeFile `
    -GatewayContainerName $gatewayContainerName `
    -GatewayImage $(if ($useGhcrImages) { $GatewayImage } else { 'easy-protocol/easy-protocol:local' }) `
    -ConfigDir $configDir `
    -DataDir $dataDir `
    -GatewayHostPort $GatewayHostPort `
    -ProviderRuntimePlans $providerRuntimePlans `
    -ProviderEnvFiles $providerEnvFiles `
    -RegisterOutputDirHost $registerOutputDirHost `
    -RegisterTeamAuthDirHost $registerTeamAuthDirHost `
    -RegisterTeamLocalDirHost $registerTeamLocalDirHost `
    -SelectedPythonContainerName $managerContainerName `
    -PythonManagerHostPort $PythonManagerHostPort

docker compose -p easy-protocol -f $composeFile down --remove-orphans | Out-Null
docker compose -p easy-protocol -f $composeFile up -d | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start isolated easyprotocol compose project"
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
    providerContainers    = @($providerRuntimePlans | ForEach-Object { $_.containerName })
    managerBaseUrl        = $managerBaseUrl
    gatewayContainerName  = $gatewayContainerName
    gatewayBaseUrl        = $gatewayBaseUrl
    managerPool           = $managerPool
    gatewayHealthStatus   = $gatewayHealth.status
    gatewayInvokeStatus   = $gatewayInvoke.status
    gatewayInvokeResult   = $gatewayInvoke.result
} | ConvertTo-Json -Depth 20
