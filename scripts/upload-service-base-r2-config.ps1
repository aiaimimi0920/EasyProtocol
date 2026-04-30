param(
    [string]$ConfigPath = 'config.yaml',
    [string]$AccountId = '',
    [string]$Bucket = '',
    [string]$AccessKeyId = '',
    [string]$SecretAccessKey = '',
    [string]$ConfigObjectKey = '',
    [string]$RuntimeEnvObjectKey = '',
    [string]$ManifestObjectKey = '',
    [string]$Endpoint = '',
    [string]$ReleaseVersion = '',
    [string]$ManifestOutput = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/easyprotocol-config.ps1')

if ([string]::IsNullOrWhiteSpace($AccountId)) {
    throw 'AccountId is required.'
}
if ([string]::IsNullOrWhiteSpace($Bucket)) {
    throw 'Bucket is required.'
}
if ([string]::IsNullOrWhiteSpace($AccessKeyId)) {
    throw 'AccessKeyId is required.'
}
if ([string]::IsNullOrWhiteSpace($SecretAccessKey)) {
    throw 'SecretAccessKey is required.'
}
if ([string]::IsNullOrWhiteSpace($ConfigObjectKey)) {
    throw 'ConfigObjectKey is required.'
}
if ([string]::IsNullOrWhiteSpace($RuntimeEnvObjectKey)) {
    throw 'RuntimeEnvObjectKey is required.'
}
if ([string]::IsNullOrWhiteSpace($ManifestObjectKey)) {
    throw 'ManifestObjectKey is required.'
}

$resolvedConfigPath = Resolve-EasyProtocolPath -Path $ConfigPath
$renderServiceOutput = Join-Path $env:TEMP ("easyprotocol-service-base-runtime-config-{0}.yaml" -f [guid]::NewGuid().ToString('N'))
$renderEnvOutput = Join-Path $env:TEMP ("easyprotocol-service-base-runtime-env-{0}.env" -f [guid]::NewGuid().ToString('N'))

try {
    & (Join-Path $PSScriptRoot 'render-derived-configs.ps1') `
        -ConfigPath $resolvedConfigPath `
        -ServiceBase `
        -ServiceOutput $renderServiceOutput `
        -ServiceEnvOutput $renderEnvOutput

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to render service/base runtime config with exit code $LASTEXITCODE"
    }

    $pythonScript = Join-Path $PSScriptRoot 'upload-service-base-r2-config.py'
    $pythonArgs = @(
        $pythonScript,
        '--account-id', $AccountId,
        '--bucket', $Bucket,
        '--access-key-id', $AccessKeyId,
        '--secret-access-key', $SecretAccessKey,
        '--config-path', $renderServiceOutput,
        '--config-object-key', $ConfigObjectKey,
        '--runtime-env-path', $renderEnvOutput,
        '--runtime-env-object-key', $RuntimeEnvObjectKey,
        '--manifest-object-key', $ManifestObjectKey
    )
    if (-not [string]::IsNullOrWhiteSpace($Endpoint)) {
        $pythonArgs += @('--endpoint', $Endpoint)
    }
    if (-not [string]::IsNullOrWhiteSpace($ReleaseVersion)) {
        $pythonArgs += @('--release-version', $ReleaseVersion)
    }
    if (-not [string]::IsNullOrWhiteSpace($ManifestOutput)) {
        $resolvedManifestOutput = if ([System.IO.Path]::IsPathRooted($ManifestOutput)) {
            $ManifestOutput
        } else {
            Join-Path (Get-EasyProtocolRepoRoot) $ManifestOutput
        }
        $pythonArgs += @('--manifest-output', $resolvedManifestOutput)
    }

    Assert-EasyProtocolPythonModule -ModuleName 'boto3' -PackageName 'boto3'
    Assert-EasyProtocolPythonModule -ModuleName 'yaml' -PackageName 'pyyaml'
    & python @pythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "R2 upload failed with exit code $LASTEXITCODE"
    }
} finally {
    Remove-Item -LiteralPath $renderServiceOutput -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $renderEnvOutput -ErrorAction SilentlyContinue
}
