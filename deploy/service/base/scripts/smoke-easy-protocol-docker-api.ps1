param(
[string]$Image = 'easy-protocol/easy-protocol:local',
  [string]$ConfigPath = 'deploy/service/base/config/config.yaml',
  [int]$HostPort = 0
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..\\..")
$resolvedConfigPath = Resolve-Path (Join-Path $repoRoot $ConfigPath)
if (-not (Test-Path -LiteralPath $resolvedConfigPath)) {
  throw "Missing rendered config: $resolvedConfigPath"
}

$smokeRoot = Join-Path $repoRoot ".tmp\\easyprotocol-smoke"
$instanceId = [Guid]::NewGuid().ToString("N")
$instanceRoot = Join-Path $smokeRoot $instanceId
$configDir = Join-Path $instanceRoot "config"
$dataDir = Join-Path $instanceRoot "data"
$containerName = "easyprotocol-smoke-$instanceId"

if ($HostPort -le 0) {
  $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  $listener.Start()
  try {
    $HostPort = $listener.LocalEndpoint.Port
  } finally {
    $listener.Stop()
  }
}

New-Item -ItemType Directory -Force -Path $configDir | Out-Null
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$smokeConfigPath = Join-Path $configDir "config.yaml"
Copy-Item -LiteralPath $resolvedConfigPath -Destination $smokeConfigPath -Force
@'
from pathlib import Path
import yaml

path = Path(r"__SMOKE_CONFIG_PATH__")
data = yaml.safe_load(path.read_text(encoding="utf-8"))
provider_pool = (((data.get("serviceBase") or {}).get("runtime") or {}).get("provider_pool") or {}).get("providers") or {}
for family in provider_pool.values():
    if isinstance(family, dict) and "warm_replicas" in family:
        family["warm_replicas"] = 0
path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
'@.Replace('__SMOKE_CONFIG_PATH__', $smokeConfigPath.Replace('\', '\\')) | python -

try {
  $dockerArgs = @(
    'run', '-d',
    '--name', $containerName,
    '-p', "${HostPort}:9788",
    '-v', "${configDir}:/etc/easy-protocol",
    '-v', "${dataDir}:/var/lib/easy-protocol"
  )
  if (Test-Path -LiteralPath '/var/run/docker.sock') {
    $dockerArgs += @('-v', '/var/run/docker.sock:/var/run/docker.sock')
  }
  $dockerArgs += $Image
  $containerId = & docker @dockerArgs
  if ($LASTEXITCODE -ne 0) {
    throw "docker run failed with exit code $LASTEXITCODE"
  }
  if ([string]::IsNullOrWhiteSpace([string]$containerId)) {
    throw 'docker run did not return a container id'
  }

  $health = $null
  $status = $null
  $lastError = $null
  for ($attempt = 1; $attempt -le 20; $attempt += 1) {
    try {
      $health = Invoke-RestMethod -Uri "http://127.0.0.1:${HostPort}/api/health" -Method Get
      $status = Invoke-RestMethod -Uri "http://127.0.0.1:${HostPort}/api/public/status" -Method Get
      $lastError = $null
      break
    } catch {
      $lastError = $_.Exception.Message
      Start-Sleep -Seconds 1
    }
  }

  if ($null -eq $health -or $null -eq $status) {
    throw "smoke health check failed: $lastError"
  }

  Write-Host "[easy-protocol] health: $($health.status)"
  Write-Host "[easy-protocol] available: $($status.available)"
} finally {
  docker rm -f $containerName 2>$null | Out-Null
  if (Test-Path -LiteralPath $instanceRoot) {
    try {
      Remove-Item -LiteralPath $instanceRoot -Recurse -Force -ErrorAction Stop
    } catch {
      Write-Warning "Failed to remove smoke temp directory '$instanceRoot': $($_.Exception.Message)"
    }
  }
}
