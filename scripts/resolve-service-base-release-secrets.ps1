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

$required = @(
    'EASYPROTOCOL_R2_CONFIG_ACCOUNT_ID',
    'EASYPROTOCOL_R2_CONFIG_BUCKET',
    'EASYPROTOCOL_R2_CONFIG_CONFIG_OBJECT_KEY',
    'EASYPROTOCOL_R2_CONFIG_ENV_OBJECT_KEY',
    'EASYPROTOCOL_R2_CONFIG_MANIFEST_OBJECT_KEY',
    'EASYPROTOCOL_R2_CONFIG_UPLOAD_ACCESS_KEY_ID',
    'EASYPROTOCOL_R2_CONFIG_UPLOAD_SECRET_ACCESS_KEY',
    'EASYPROTOCOL_R2_CONFIG_READ_ACCESS_KEY_ID',
    'EASYPROTOCOL_R2_CONFIG_READ_SECRET_ACCESS_KEY'
)

foreach ($name in $required) {
    $value = [string]$resolved[$name]
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Missing required EasyProtocol release secret after fallback resolution: $name"
    }
}

$importCodePublicKey = Get-ResolvedSecretValue @(
    'EASYPROTOCOL_IMPORT_CODE_OWNER_PUBLIC_KEY'
)

foreach ($entry in $resolved.GetEnumerator()) {
    Append-KeyValueFile -Path $GithubEnvPath -Name $entry.Key -Value ([string]$entry.Value)
}
Append-KeyValueFile -Path $GithubEnvPath -Name 'EASYPROTOCOL_IMPORT_CODE_OWNER_PUBLIC_KEY' -Value $importCodePublicKey

$importCodeEnabled = if ([string]::IsNullOrWhiteSpace($importCodePublicKey)) { 'false' } else { 'true' }

Append-KeyValueFile -Path $GithubOutputPath -Name 'manifest_object_key' -Value ([string]$resolved['EASYPROTOCOL_R2_CONFIG_MANIFEST_OBJECT_KEY'])
Append-KeyValueFile -Path $GithubOutputPath -Name 'config_object_key' -Value ([string]$resolved['EASYPROTOCOL_R2_CONFIG_CONFIG_OBJECT_KEY'])
Append-KeyValueFile -Path $GithubOutputPath -Name 'runtime_env_object_key' -Value ([string]$resolved['EASYPROTOCOL_R2_CONFIG_ENV_OBJECT_KEY'])
Append-KeyValueFile -Path $GithubOutputPath -Name 'import_code_enabled' -Value $importCodeEnabled

Write-Host ('Resolved service/base R2 config secrets. bucket={0} manifest={1} importCode={2}' -f `
    $resolved['EASYPROTOCOL_R2_CONFIG_BUCKET'], `
    $resolved['EASYPROTOCOL_R2_CONFIG_MANIFEST_OBJECT_KEY'], `
    $importCodeEnabled) -ForegroundColor Green
