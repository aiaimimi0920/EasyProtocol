param(
    [string]$GithubEnvPath = '',
    [string]$GithubOutputPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-ResolvedSecretValue {
    param(
        [string[]]$Names,
        [string]$Default = ''
    )

    foreach ($name in $Names) {
        if ([string]::IsNullOrWhiteSpace($name)) {
            continue
        }

        $item = Get-Item -Path "Env:$name" -ErrorAction SilentlyContinue
        if ($null -eq $item) {
            continue
        }

        $value = [string]$item.Value
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim()
        }
    }

    return $Default
}

function Append-KeyValueFile {
    param(
        [string]$Path,
        [string]$Name,
        [AllowEmptyString()]
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }

    Add-Content -LiteralPath $Path -Value ("{0}={1}" -f $Name, $Value) -Encoding utf8
}

$resolved = [ordered]@{
    EASYPROTOCOL_R2_CONFIG_ACCOUNT_ID = Get-ResolvedSecretValue @(
        'EASYPROTOCOL_R2_CONFIG_ACCOUNT_ID',
        'EASYPROTOCOL_PROVIDER_REGISTER_R2_ACCOUNT_ID'
    )
    EASYPROTOCOL_R2_CONFIG_BUCKET = Get-ResolvedSecretValue @(
        'EASYPROTOCOL_R2_CONFIG_BUCKET',
        'EASYPROTOCOL_PROVIDER_REGISTER_R2_BUCKET'
    )
    EASYPROTOCOL_R2_CONFIG_ENDPOINT = Get-ResolvedSecretValue @(
        'EASYPROTOCOL_R2_CONFIG_ENDPOINT',
        'EASYPROTOCOL_PROVIDER_REGISTER_R2_ENDPOINT_URL'
    )
    EASYPROTOCOL_R2_CONFIG_CONFIG_OBJECT_KEY = Get-ResolvedSecretValue @(
        'EASYPROTOCOL_R2_CONFIG_CONFIG_OBJECT_KEY'
    ) 'easyprotocol/service-base/config.yaml'
    EASYPROTOCOL_R2_CONFIG_ENV_OBJECT_KEY = Get-ResolvedSecretValue @(
        'EASYPROTOCOL_R2_CONFIG_ENV_OBJECT_KEY'
    ) 'easyprotocol/service-base/runtime.env'
    EASYPROTOCOL_R2_CONFIG_MANIFEST_OBJECT_KEY = Get-ResolvedSecretValue @(
        'EASYPROTOCOL_R2_CONFIG_MANIFEST_OBJECT_KEY'
    ) 'easyprotocol/service-base/distribution-manifest.json'
    EASYPROTOCOL_R2_CONFIG_UPLOAD_ACCESS_KEY_ID = Get-ResolvedSecretValue @(
        'EASYPROTOCOL_R2_CONFIG_UPLOAD_ACCESS_KEY_ID',
        'EASYPROTOCOL_PROVIDER_REGISTER_R2_ACCESS_KEY_ID'
    )
    EASYPROTOCOL_R2_CONFIG_UPLOAD_SECRET_ACCESS_KEY = Get-ResolvedSecretValue @(
        'EASYPROTOCOL_R2_CONFIG_UPLOAD_SECRET_ACCESS_KEY',
        'EASYPROTOCOL_PROVIDER_REGISTER_R2_SECRET_ACCESS_KEY'
    )
    EASYPROTOCOL_R2_CONFIG_READ_ACCESS_KEY_ID = Get-ResolvedSecretValue @(
        'EASYPROTOCOL_R2_CONFIG_READ_ACCESS_KEY_ID',
        'EASYPROTOCOL_R2_CONFIG_UPLOAD_ACCESS_KEY_ID',
        'EASYPROTOCOL_PROVIDER_REGISTER_R2_ACCESS_KEY_ID'
    )
    EASYPROTOCOL_R2_CONFIG_READ_SECRET_ACCESS_KEY = Get-ResolvedSecretValue @(
        'EASYPROTOCOL_R2_CONFIG_READ_SECRET_ACCESS_KEY',
        'EASYPROTOCOL_R2_CONFIG_UPLOAD_SECRET_ACCESS_KEY',
        'EASYPROTOCOL_PROVIDER_REGISTER_R2_SECRET_ACCESS_KEY'
    )
}

$runtimeConfigRequired = @(
    'EASYPROTOCOL_R2_CONFIG_ACCOUNT_ID',
    'EASYPROTOCOL_R2_CONFIG_BUCKET',
    'EASYPROTOCOL_R2_CONFIG_CONFIG_OBJECT_KEY',
    'EASYPROTOCOL_R2_CONFIG_ENV_OBJECT_KEY',
    'EASYPROTOCOL_R2_CONFIG_MANIFEST_OBJECT_KEY',
    'EASYPROTOCOL_R2_CONFIG_UPLOAD_ACCESS_KEY_ID',
    'EASYPROTOCOL_R2_CONFIG_UPLOAD_SECRET_ACCESS_KEY'
)

$missingRuntimeConfigSecrets = @()
foreach ($name in $runtimeConfigRequired) {
    $value = [string]$resolved[$name]
    if ([string]::IsNullOrWhiteSpace($value)) {
        $missingRuntimeConfigSecrets += $name
    }
}

$importCodePublicKey = Get-ResolvedSecretValue @(
    'EASYPROTOCOL_IMPORT_CODE_OWNER_PUBLIC_KEY'
)
$runtimeConfigEnabled = ($missingRuntimeConfigSecrets.Count -eq 0)
$readKeysAvailable = (
    -not [string]::IsNullOrWhiteSpace([string]$resolved['EASYPROTOCOL_R2_CONFIG_READ_ACCESS_KEY_ID']) -and
    -not [string]::IsNullOrWhiteSpace([string]$resolved['EASYPROTOCOL_R2_CONFIG_READ_SECRET_ACCESS_KEY'])
)
$importCodeEnabled = $runtimeConfigEnabled -and $readKeysAvailable -and (-not [string]::IsNullOrWhiteSpace($importCodePublicKey))

foreach ($entry in $resolved.GetEnumerator()) {
    Append-KeyValueFile -Path $GithubEnvPath -Name $entry.Key -Value ([string]$entry.Value)
}
Append-KeyValueFile -Path $GithubEnvPath -Name 'EASYPROTOCOL_IMPORT_CODE_OWNER_PUBLIC_KEY' -Value $importCodePublicKey

Append-KeyValueFile -Path $GithubOutputPath -Name 'manifest_object_key' -Value ([string]$resolved['EASYPROTOCOL_R2_CONFIG_MANIFEST_OBJECT_KEY'])
Append-KeyValueFile -Path $GithubOutputPath -Name 'config_object_key' -Value ([string]$resolved['EASYPROTOCOL_R2_CONFIG_CONFIG_OBJECT_KEY'])
Append-KeyValueFile -Path $GithubOutputPath -Name 'runtime_env_object_key' -Value ([string]$resolved['EASYPROTOCOL_R2_CONFIG_ENV_OBJECT_KEY'])
Append-KeyValueFile -Path $GithubOutputPath -Name 'runtime_config_enabled' -Value ($(if ($runtimeConfigEnabled) { 'true' } else { 'false' }))
Append-KeyValueFile -Path $GithubOutputPath -Name 'import_code_enabled' -Value ($(if ($importCodeEnabled) { 'true' } else { 'false' }))
Append-KeyValueFile -Path $GithubOutputPath -Name 'missing_runtime_config_secrets' -Value (($missingRuntimeConfigSecrets -join ','))

if (-not $runtimeConfigEnabled) {
    Write-Warning ('Skipping service/base R2 config distribution because no complete secret set was resolved. missing={0}' -f ($missingRuntimeConfigSecrets -join ','))
} else {
    Write-Host ('Resolved service/base R2 config secrets. bucket={0} manifest={1}' -f `
        $resolved['EASYPROTOCOL_R2_CONFIG_BUCKET'], `
        $resolved['EASYPROTOCOL_R2_CONFIG_MANIFEST_OBJECT_KEY']) -ForegroundColor Green
}

if (-not $importCodeEnabled) {
    Write-Warning 'Encrypted import-code generation disabled because runtime config or read credentials or public key is unavailable.'
}
