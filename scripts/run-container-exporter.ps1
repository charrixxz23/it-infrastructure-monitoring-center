$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$context = & docker context show
if ($LASTEXITCODE -ne 0 -or $context.Trim() -ne "desktop-linux") {
    throw "Docker context must be desktop-linux for this local exporter. No Docker context was changed."
}
& py -3 --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Python 3 is required. Install Python; this script does not install packages." }

Write-Host "Starting the read-only container exporter on 127.0.0.1:9464. Press Ctrl+C to stop it."
Write-Host "The process uses the current user's Docker Desktop access; see docs/container-exporter.md."
& py -3 -B services/container-exporter/container_exporter.py
if ($LASTEXITCODE -ne 0) { throw "Container exporter exited with status $LASTEXITCODE." }
