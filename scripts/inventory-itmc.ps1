$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCommand) {
    Write-Error 'Python 3.10 or newer is required; no Docker command was attempted.'
    exit 2
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$helper = Join-Path $PSScriptRoot 'backup\backup_itmc.py'

Push-Location $repoRoot
try {
    & python $helper inventory
    $nativeExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($null -eq $nativeExitCode -or $nativeExitCode -ne 0) {
    exit 2
}
exit 0
