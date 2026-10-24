$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "Running Docker exporter unit tests with mocked Docker responses."
& py -3 -m unittest discover -s services/container-exporter -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw "Container exporter unit tests failed." }

$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 9464 -ErrorAction SilentlyContinue)
if ($listeners.Count -eq 0) { throw "Exporter is not listening on port 9464. Run .\scripts\run-container-exporter.ps1 in a separate PowerShell window." }
$listenAddresses = @($listeners | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique)
if ($listenAddresses.Count -ne 1 -or $listenAddresses[0] -ne "127.0.0.1") {
    throw "Exporter must listen only on 127.0.0.1; observed listener addresses: $($listenAddresses -join ', ')."
}
$health = [string](Invoke-RestMethod -Uri "http://127.0.0.1:9464/health" -TimeoutSec 3)
if ($health.Trim() -ne "ok") { throw "Exporter health endpoint did not return the expected liveness response." }
$writeStatus = 0
try {
    $writeResponse = Invoke-WebRequest -Uri "http://127.0.0.1:9464/metrics" -Method Post -Body "ignored" -TimeoutSec 3
    $writeStatus = [int]$writeResponse.StatusCode
} catch {
    if ($_.Exception.Response) { $writeStatus = [int]$_.Exception.Response.StatusCode.value__ }
}
if ($writeStatus -ne 405) { throw "Exporter must reject HTTP write methods with 405; observed $writeStatus." }
$metricsResponse = Invoke-WebRequest -Uri "http://127.0.0.1:9464/metrics" -TimeoutSec 5
$metricsText = [string]$metricsResponse.Content
if ($metricsResponse.StatusCode -ne 200 -or $metricsResponse.Headers["Content-Type"] -notlike "text/plain; version=0.0.4*") {
    throw "Exporter metrics endpoint returned an unexpected response."
}
if ($metricsText -match "container_name|image=|/var/lib|(?i)password|(?i)token|(?i)secret") {
    throw "Exporter metrics include a forbidden metadata pattern."
}
$localSamples = @([regex]::Matches($metricsText, '(?m)^itmc_container_info\{service="[A-Za-z0-9_.-]+",container_id="[0-9a-f]{12}"\} 1$'))
if ($localSamples.Count -lt 1 -or $localSamples.Count -gt 32) { throw "Expected 1–32 project-scoped container observations; got $($localSamples.Count)." }
Write-Host "PASS: exporter responds on loopback and exposes $($localSamples.Count) scoped container samples without full names or environment metadata."

function Get-ApiJson([string]$Url, [switch]$Authenticated) {
    $headers = @{}
    if ($Authenticated) {
        $settings = @{}
        foreach ($line in Get-Content -LiteralPath ".env") {
            if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)$') {
                $value = $Matches[2].Trim()
                if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
                $settings[$Matches[1]] = $value
            }
        }
        $user = $settings["GF_SECURITY_ADMIN_USER"]
        $password = $settings["GF_SECURITY_ADMIN_PASSWORD"]
        if (-not $user -or -not $password -or $password -eq "REPLACE_WITH_A_LONG_RANDOM_PASSWORD") { throw "Set local Grafana credentials in .env." }
        $credentialBytes = [Text.Encoding]::UTF8.GetBytes($user + ":" + $password)
        $headers.Authorization = "Basic " + [Convert]::ToBase64String($credentialBytes)
    }
    return Invoke-RestMethod -Uri $Url -Headers $headers -TimeoutSec 10
}

function Get-PromSeries([string]$Expression) {
    $encoded = [Uri]::EscapeDataString($Expression)
    $response = Get-ApiJson "http://127.0.0.1:9090/api/v1/query?query=$encoded"
    if ($response.status -ne "success") { throw "Prometheus query failed for the container exporter." }
    return @($response.data.result)
}

$targetsResponse = Get-ApiJson "http://127.0.0.1:9090/api/v1/targets"
$exporterTargets = @($targetsResponse.data.activeTargets | Where-Object { $_.labels.job -eq "container-exporter" })
if ($exporterTargets.Count -ne 1 -or $exporterTargets[0].health -ne "up" -or $exporterTargets[0].scrapeUrl -ne "http://host.docker.internal:9464/metrics") { throw "Prometheus container-exporter target is missing, not UP, or targets an unexpected URL." }
Write-Host "PASS: Prometheus reaches the loopback-only exporter through host.docker.internal."

$success = Get-PromSeries 'itmc_container_exporter_collection_success{job="container-exporter"}'
if ($success.Count -ne 1 -or [double]$success[0].value[1] -ne 1) { throw "Docker stats collection is not reporting success." }
$observed = Get-PromSeries 'itmc_container_exporter_observed_containers{job="container-exporter"}'
$cpu = Get-PromSeries 'itmc_container_cpu_usage_percent{job="container-exporter"}'
$memory = Get-PromSeries 'itmc_container_memory_usage_bytes{job="container-exporter"}'
$rx = Get-PromSeries 'itmc_container_network_receive_bytes_total{job="container-exporter"}'
$tx = Get-PromSeries 'itmc_container_network_transmit_bytes_total{job="container-exporter"}'
$observedCount = [int][double]$observed[0].value[1]
if ($observedCount -lt 1 -or $observedCount -gt 32 -or $cpu.Count -ne $observedCount -or $memory.Count -ne $observedCount) {
    throw "Prometheus observed-container, CPU, and memory series counts are inconsistent."
}
if ($rx.Count -lt 1 -or $tx.Count -lt 1) { throw "Docker stats network counters are not available for the observed containers." }
Write-Host "PASS: Prometheus has real CPU and memory series for $observedCount containers; RX/TX counters are present."

$sampleAge = Get-PromSeries 'time() - itmc_container_exporter_last_success_timestamp_seconds{job="container-exporter"}'
if ($sampleAge.Count -ne 1 -or [double]$sampleAge[0].value[1] -gt 45) { throw "Prometheus has no sufficiently fresh complete exporter sample." }
Write-Host "PASS: last complete Docker stats sample is fresh."

$dashboard = Get-ApiJson "http://127.0.0.1:3000/api/dashboards/uid/docker-container-monitoring" -Authenticated
if ($dashboard.dashboard.title -ne "Docker Container Monitoring" -or -not $dashboard.meta.provisioned -or $dashboard.meta.provisionedExternalId -ne "docker-container-monitoring.json") {
    throw "Grafana has not provisioned Docker Container Monitoring from the dashboard file."
}
$dashboardFile = Get-Content -Raw -LiteralPath "config/grafana/dashboards/docker-container-monitoring.json" | ConvertFrom-Json
if (@($dashboardFile.panels).Count -ne 12) { throw "Docker Container Monitoring JSON should contain 12 panels." }
$expectedUnits = @{
    "Total CPU Usage" = "percent"
    "Total Memory Used" = "bytes"
    "Network RX" = "binBps"
    "Network TX" = "binBps"
    "CPU Usage by Container" = "percent"
    "Memory Usage by Container" = "bytes"
    "Network Throughput by Container" = "binBps"
    "Last Complete Sample Age" = "s"
}
foreach ($title in $expectedUnits.Keys) {
    $panel = $dashboardFile.panels | Where-Object { $_.title -eq $title }
    if (-not $panel -or $panel.fieldConfig.defaults.unit -ne $expectedUnits[$title]) { throw "Dashboard panel '$title' has an unexpected unit." }
}
$statusPanelTitles = @("Compose Containers Observed", "Exporter Health", "Last Complete Sample Age", "Docker Stats Collection")
foreach ($title in $statusPanelTitles) {
    $panel = @($dashboardFile.panels | Where-Object { $_.title -eq $title })
    if ($panel.Count -ne 1 -or $panel[0].type -ne "stat") { throw "Expected exactly one Stat panel for '$title'." }
    $target = @($panel[0].targets)
    if ($target.Count -ne 1 -or $target[0].instant -ne $true -or $target[0].range -ne $false) {
        throw "Status panel '$title' must use one instant-only Prometheus query."
    }
    if ($panel[0].options.reduceOptions.values -ne $false -or $panel[0].options.reduceOptions.calcs -notcontains "lastNotNull") {
        throw "Status panel '$title' must reduce to the last non-null value, not display all values."
    }
    if ($panel[0].fieldConfig.defaults.noValue -ne "NO DATA" -or $target[0].expr -match 'or\s+vector\s*\(\s*0\s*\)') {
        throw "Status panel '$title' must show NO DATA on absence and must not synthesize a zero."
    }
    if ($null -ne $panel[0].transformations -and @($panel[0].transformations).Count -gt 0) { throw "Status panel '$title' must not contain a transformation that can duplicate status values." }
}
$queryCount = 0
$panelQueryCounts = @{}
foreach ($panel in $dashboardFile.panels) {
    $panelQueryCounts[$panel.title] = 0
    foreach ($target in $panel.targets) {
        if ($target.expr) {
            $expression = $target.expr.Replace('$__rate_interval', '1m')
            $series = Get-PromSeries $expression
            if ($series.Count -eq 0) { throw "Dashboard PromQL returned no data: $($panel.title)." }
            $queryCount++
            $panelQueryCounts[$panel.title]++
        }
    }
}
foreach ($title in $panelQueryCounts.Keys) {
    if ($panelQueryCounts[$title] -lt 1) { throw "Dashboard panel '$title' has no PromQL query to validate." }
}
$loadedExpressions = @($dashboard.dashboard.panels | ForEach-Object { $_.targets | ForEach-Object { $_.expr } } | Where-Object { $_ })
if ($loadedExpressions.Count -ne $queryCount) { throw "Grafana's provisioned dashboard query count differs from its JSON file." }
$dashboardFileExpressions = @($dashboardFile.panels | ForEach-Object { $_.targets | ForEach-Object { $_.expr } } | Where-Object { $_ })
if (@(Compare-Object $dashboardFileExpressions $loadedExpressions).Count -gt 0) { throw "Grafana's provisioned dashboard PromQL differs from the file." }
$dashboardPanels = @($dashboard.dashboard.panels)
$loadedStatusTitles = @("Compose Containers Observed", "Exporter Health", "Last Complete Sample Age", "Docker Stats Collection")
foreach ($title in $loadedStatusTitles) {
    $panel = @($dashboardPanels | Where-Object { $_.title -eq $title })
    if ($panel.Count -ne 1 -or $panel[0].targets.Count -ne 1 -or $panel[0].targets[0].instant -ne $true -or $panel[0].targets[0].range -ne $false -or $panel[0].options.reduceOptions.values -ne $false -or $panel[0].options.reduceOptions.calcs -notcontains "lastNotNull" -or $panel[0].fieldConfig.defaults.noValue -ne "NO DATA" -or ($null -ne $panel[0].transformations -and @($panel[0].transformations).Count -gt 0)) {
        throw "Grafana's provisioned status panel '$title' is not using the expected instant/current-value behavior."
    }
}
$healthMapping = ($dashboardFile.panels | Where-Object { $_.title -eq "Exporter Health" }).fieldConfig.defaults.mappings[0].options
$collectionMapping = ($dashboardFile.panels | Where-Object { $_.title -eq "Docker Stats Collection" }).fieldConfig.defaults.mappings[0].options
$loadedHealthMapping = ($dashboardPanels | Where-Object { $_.title -eq "Exporter Health" }).fieldConfig.defaults.mappings[0].options
$loadedCollectionMapping = ($dashboardPanels | Where-Object { $_.title -eq "Docker Stats Collection" }).fieldConfig.defaults.mappings[0].options
if ($healthMapping.'0'.text -ne "DOWN" -or $healthMapping.'1'.text -ne "UP" -or $collectionMapping.'0'.text -ne "ERROR" -or $collectionMapping.'1'.text -ne "OK" -or $loadedHealthMapping.'0'.text -ne "DOWN" -or $loadedHealthMapping.'1'.text -ne "UP" -or $loadedCollectionMapping.'0'.text -ne "ERROR" -or $loadedCollectionMapping.'1'.text -ne "OK") {
    throw "Grafana status value mappings must preserve the distinct DOWN/UP and ERROR/OK meanings."
}
foreach ($title in $statusPanelTitles) {
    if ($panelQueryCounts[$title] -ne 1) { throw "Expected one live PromQL expression for status panel '$title'." }
    $expression = (@($dashboardFile.panels | Where-Object { $_.title -eq $title })[0].targets[0].expr).Replace('$__rate_interval', '1m')
    $series = Get-PromSeries $expression
    if ($series.Count -ne 1) { throw "Status panel '$title' must return exactly one current series; Prometheus returned $($series.Count)." }
}
$countSeries = Get-PromSeries 'max(itmc_container_exporter_observed_containers{job="container-exporter"} and on(job,instance) (itmc_container_exporter_collection_success{job="container-exporter"} == 1) and on(job,instance) (up{job="container-exporter"} == 1))'
$healthSeries = Get-PromSeries 'min(up{job="container-exporter"})'
$collectionSeries = Get-PromSeries 'min(itmc_container_exporter_collection_success{job="container-exporter"} and on(job,instance) (up{job="container-exporter"} == 1))'
$ageSeries = Get-PromSeries 'max((time() - itmc_container_exporter_last_success_timestamp_seconds{job="container-exporter"}) and on(job,instance) (up{job="container-exporter"} == 1))'
if ([int][double]$countSeries[0].value[1] -ne $observedCount -or [double]$healthSeries[0].value[1] -ne 1 -or [double]$collectionSeries[0].value[1] -ne 1 -or [double]$ageSeries[0].value[1] -gt 45) {
    throw "Current dashboard state must match real Prometheus observations (count, scrape health, Docker collection, freshness)."
}
$exporterJobTarget = @($targetsResponse.data.activeTargets | Where-Object { $_.labels.job -eq "container-exporter" })[0]
$targetScrapeAgeSeconds = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($exporterJobTarget.lastScrape)).TotalSeconds
if ($exporterJobTarget.lastError -or $targetScrapeAgeSeconds -gt 45 -or $targetScrapeAgeSeconds -lt 0) {
    throw "Prometheus exporter scrape is not clean and fresh."
}
$originalDashboard = Get-ApiJson "http://127.0.0.1:3000/api/dashboards/uid/it-infra-overview" -Authenticated
if ($originalDashboard.dashboard.title -ne "IT Infrastructure Overview" -or @($originalDashboard.dashboard.panels).Count -ne 14) {
    throw "The existing 14-panel infrastructure dashboard changed unexpectedly."
}
$panels = $dashboardPanels
if ($panels.Count -ne 12) { throw "Docker Container Monitoring should have 12 panels; Grafana returned $($panels.Count)." }
foreach ($title in @("Compose Containers Observed", "Total CPU Usage", "Total Memory Used", "Network RX", "Network TX", "Exporter Health", "CPU Usage by Container", "Memory Usage by Container", "Network Throughput by Container", "Container Observation Age", "Last Complete Sample Age", "Docker Stats Collection")) {
    if ($panels.title -notcontains $title) { throw "Grafana dashboard is missing panel '$title'." }
}
Write-Host "PASS: Grafana provisioned the independent 12-panel Docker Container Monitoring dashboard; all $queryCount PromQL expressions across $($panelQueryCounts.Count) panels match and return data."
Write-Host "PASS: state cards return one current series each (observed=$observedCount, target UP=1, Docker collection=1); last complete sample age is $([math]::Round([double]$ageSeries[0].value[1], 1))s."
Write-Host "PASS: exporter target scrape age is $([math]::Round($targetScrapeAgeSeconds, 1))s and status mappings remain distinct."
Write-Host "PASS: the existing IT Infrastructure Overview dashboard still has 14 panels."
