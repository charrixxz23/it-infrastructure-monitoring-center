$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
python "$PSScriptRoot\simulate_incident.py" @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
