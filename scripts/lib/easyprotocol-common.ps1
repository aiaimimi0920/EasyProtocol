Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-EasyProtocolCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [string]$Hint = ''
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        if ([string]::IsNullOrWhiteSpace($Hint)) {
            throw "Required command not found: $Name"
        }
        throw "Required command not found: $Name. $Hint"
    }
}

function Invoke-EasyProtocolExternalCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$Arguments = @(),

        [string]$WorkingDirectory = '',

        [string]$FailureMessage = ''
    )

    $previous = $null
    if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
        $previous = Get-Location
        Push-Location $WorkingDirectory
    }

    try {
        $capturePath = [System.Environment]::GetEnvironmentVariable('EASYPROTOCOL_TEST_CAPTURE_EXTERNAL_COMMANDS_PATH')
        if (-not [string]::IsNullOrWhiteSpace($capturePath)) {
            $record = [pscustomobject]@{
                FilePath         = $FilePath
                Arguments        = @($Arguments)
                WorkingDirectory = $WorkingDirectory
                FailureMessage   = $FailureMessage
            }
            $line = $record | ConvertTo-Json -Compress -Depth 5
            Add-Content -LiteralPath $capturePath -Value $line -Encoding UTF8
            return
        }

        $extension = [System.IO.Path]::GetExtension($FilePath)
        if ($extension -ieq '.ps1') {
            $invokeArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $FilePath) + @($Arguments)
            & powershell @invokeArgs
        }
        else {
            & $FilePath @Arguments
        }
        if ($LASTEXITCODE -ne 0) {
            if ([string]::IsNullOrWhiteSpace($FailureMessage)) {
                throw ("Command failed with exit code {0}: {1} {2}" -f $LASTEXITCODE, $FilePath, ($Arguments -join ' '))
            }
            throw "$FailureMessage (exit code $LASTEXITCODE)"
        }
    }
    finally {
        if ($null -ne $previous) {
            Pop-Location
        }
    }
}
