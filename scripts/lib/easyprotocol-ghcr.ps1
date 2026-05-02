Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-EasyProtocolGhcrOwnerReady {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Owner,

        [string]$SourceDescription = 'GHCR owner'
    )

    $normalized = $Owner.Trim()
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        throw "$SourceDescription is empty. Set publishing.ghcr.owner in config.yaml or pass -GhcrOwner explicitly."
    }

    if ($normalized -match '^(your-github-owner|change_me.*|.*placeholder.*)$') {
        throw "$SourceDescription still uses a placeholder value: $normalized"
    }
}
