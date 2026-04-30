Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Ensure-EasyProtocolExternalNetwork {
    param(
        [string]$NetworkName = 'EasyAiMi'
    )

    $existing = docker network ls --format '{{.Name}}' | Where-Object { $_ -eq $NetworkName }
    if ($LASTEXITCODE -ne 0) {
        throw "docker network ls failed with exit code $LASTEXITCODE"
    }

    if ($existing) {
        Write-Host "Docker network already exists: $NetworkName" -ForegroundColor DarkGray
        return
    }

    Write-Host "Creating docker network: $NetworkName" -ForegroundColor Cyan
    docker network create $NetworkName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "docker network create failed with exit code $LASTEXITCODE"
    }
}

