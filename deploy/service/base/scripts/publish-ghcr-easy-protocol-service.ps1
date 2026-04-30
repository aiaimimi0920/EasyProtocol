param(
  [string]$Owner = "aiaimimi0920",
  [string]$ReleaseTag = "release-local",
  [switch]$Push
)

$ErrorActionPreference = "Stop"

$workspaceRoot = Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..")
$image = "ghcr.io/$Owner/easy-protocol-service:$ReleaseTag"

Write-Host "[easy-protocol] building $image from $workspaceRoot"
docker build -t $image -f (Join-Path $workspaceRoot "deploy\\service\\base\\Dockerfile") $workspaceRoot

if ($Push) {
  Write-Host "[easy-protocol] pushing $image"
  docker push $image
}

Write-Host "[easy-protocol] done: $image"
