[CmdletBinding()]
param(
    [ValidateSet('Plan', 'BackupConfig', 'Verify')]
    [string]$Mode = 'Plan',

    [string]$Destination,
    [string]$Archive,
    [switch]$ConfirmEncryptedDestination
)

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCommand) {
    Write-Error 'Python 3.10 or newer is required; no backup operation was performed.'
    exit 2
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$helper = Join-Path $PSScriptRoot 'backup\backup_itmc.py'
$arguments = @($helper)

switch ($Mode) {
    'Plan' {
        $arguments += 'plan'
        if ($PSBoundParameters.ContainsKey('Destination')) {
            $arguments += @('--destination', $Destination)
        }
    }
    'BackupConfig' {
        if ([string]::IsNullOrWhiteSpace($Destination)) {
            Write-Error 'BackupConfig requires -Destination pointing to an existing encrypted local directory.'
            exit 2
        }
        if (-not $ConfirmEncryptedDestination) {
            Write-Error 'BackupConfig requires -ConfirmEncryptedDestination. This is an operator attestation, not an encryption check.'
            exit 2
        }
        $arguments += @('backup-config', '--destination', $Destination, '--confirm-encrypted-destination')
    }
    'Verify' {
        if ([string]::IsNullOrWhiteSpace($Archive)) {
            Write-Error 'Verify requires -Archive pointing to a config backup ZIP.'
            exit 2
        }
        $arguments += @('verify', '--archive', $Archive)
    }
}

Push-Location $repoRoot
try {
    & python @arguments
    $nativeExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($null -eq $nativeExitCode -or $nativeExitCode -ne 0) {
    exit 2
}
exit 0
