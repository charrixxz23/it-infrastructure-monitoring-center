$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
if (-not (Test-Path -LiteralPath ".env")) { throw "Copy .env.example to .env and set Grafana credentials first." }
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Docker Compose configuration is invalid." }
Write-Host "PASS: Docker Compose configuration is valid."

foreach ($check in @(
    @{ Service = "prometheus"; Args = @("promtool", "check", "config", "/etc/prometheus/prometheus.yml"); Label = "Prometheus config" },
    @{ Service = "prometheus"; Args = @("promtool", "check", "rules", "/etc/prometheus/rules/infrastructure.yml", "/etc/prometheus/test-rules/incident-test.yml"); Label = "Prometheus alert rules" },
    @{ Service = "prometheus"; Args = @("promtool", "test", "rules", "/etc/prometheus/tests/infrastructure.test.yml"); Label = "Prometheus alert rule tests" },
    @{ Service = "alertmanager"; Args = @("amtool", "check-config", "/etc/alertmanager/alertmanager.yml"); Label = "Alertmanager config" }
)) {
    & docker compose exec -T $check.Service @($check.Args)
    if ($LASTEXITCODE -ne 0) { throw "$($check.Label) check failed." }
    Write-Host "PASS: $($check.Label) is valid."
}
& python -m unittest discover -s services/incident-logger -p test_incident_logger.py
if ($LASTEXITCODE -ne 0) { throw "Incident logger webhook tests failed." }
Write-Host "PASS: incident logger webhook unit tests passed."

$settings = @{}
foreach ($line in Get-Content -LiteralPath ".env") {
    if ($line -match "^\s*([^#=\s]+)\s*=\s*(.*)$") {
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

function Get-Json($url, [switch]$Authenticated) {
    $headers = @{}
    if ($Authenticated) {
        $credentialText = $user + ":" + $password
        $bytes = [Text.Encoding]::ASCII.GetBytes($credentialText)
        $headers.Authorization = "Basic " + [Convert]::ToBase64String($bytes)
    }
    Invoke-RestMethod -Uri $url -Headers $headers -TimeoutSec 10
}

$null = Invoke-RestMethod -Uri "http://127.0.0.1:9090/-/healthy" -TimeoutSec 10
Write-Host "PASS: Prometheus availability endpoint responds."
$null = Invoke-RestMethod -Uri "http://127.0.0.1:9093/-/healthy" -TimeoutSec 10
Write-Host "PASS: Alertmanager availability endpoint responds."
$loggerHealth = docker compose exec -T incident-logger python /app/incident_logger.py --healthcheck
if ($LASTEXITCODE -ne 0) { throw "Incident logger health endpoint is unavailable." }
Write-Host "PASS: incident logger is healthy on its internal Docker network."
$alertmanagerPort = docker compose port alertmanager 9093
if ($LASTEXITCODE -ne 0 -or $alertmanagerPort -ne "127.0.0.1:9093") { throw "Alertmanager UI must be published only on 127.0.0.1:9093." }
$loggerContainer = docker compose ps -q incident-logger
$loggerBindings = docker inspect --format '{{json .HostConfig.PortBindings}}' $loggerContainer
if ($LASTEXITCODE -ne 0 -or ($loggerBindings.Trim() -ne "{}" -and $loggerBindings.Trim() -ne "null")) { throw "Incident logger must not publish a host port." }
$targets = (Get-Json "http://127.0.0.1:9090/api/v1/targets").data.activeTargets
foreach ($job in @("prometheus", "node-exporter")) {
    $found = @($targets | Where-Object { $_.labels.job -eq $job })
    if ($found.Count -eq 0 -or @($found | Where-Object { $_.health -ne "up" }).Count -gt 0) { throw "Prometheus target $job is missing or not UP." }
    Write-Host "PASS: Prometheus target $job is UP."
}
foreach ($metric in @("node_uname_info", "node_cpu_seconds_total", "node_memory_MemTotal_bytes", "node_filesystem_avail_bytes", "node_network_receive_bytes_total", "node_boot_time_seconds")) {
    $encoded = [Uri]::EscapeDataString($metric)
    $result = Get-Json "http://127.0.0.1:9090/api/v1/query?query=$encoded"
    if ($result.status -ne "success" -or @($result.data.result).Count -eq 0) { throw "Prometheus query returned no data: $metric." }
    Write-Host "PASS: real Node Exporter metric is available: $metric."
}
$ruleResponse = Get-Json "http://127.0.0.1:9090/api/v1/rules?type=alert"
$alertRules = @($ruleResponse.data.groups | ForEach-Object { $_.rules } | Where-Object { $_.type -eq "alerting" })
$expectedAlerts = @("TargetDown", "HighCPUUsage", "HighMemoryUsage", "DiskSpaceCritical", "TestIncident")
foreach ($name in $expectedAlerts) {
    if ($alertRules.name -notcontains $name) { throw "Prometheus did not load alert rule $name." }
}
$testRule = $alertRules | Where-Object { $_.name -eq "TestIncident" }
if ($testRule.query -ne "vector(0) == 1" -or @($testRule.alerts).Count -gt 0) { throw "TestIncident must remain disabled outside the controlled test." }
Write-Host "PASS: four production alert rules are loaded and TestIncident is disabled."
$dashboardFile = Get-Content -Raw -LiteralPath "config/grafana/dashboards/infrastructure-overview.json" | ConvertFrom-Json
$trendPanels = @($dashboardFile.panels | Where-Object { $_.type -eq "timeseries" } | Sort-Object { $_.gridPos.x })
if ($trendPanels.Count -ne 4) { throw "Expected four trend panels on the main trend row." }
for ($i = 0; $i -lt $trendPanels.Count; $i++) {
    $position = $trendPanels[$i].gridPos
    if ($position.y -ne 5 -or $position.x -ne ($i * 6) -or $position.w -ne 6 -or $position.h -ne 7) { throw "Trend panels are not arranged as four balanced columns for a 1920x1080 view." }
}
Write-Host "PASS: four trend panels share a balanced row for 1920x1080."
foreach ($panel in $dashboardFile.panels) {
    foreach ($target in $panel.targets) {
        if ($target.expr) {
            $expression = $target.expr.Replace('$__rate_interval', '1m')
            $encoded = [Uri]::EscapeDataString($expression)
            $queryResult = Get-Json "http://127.0.0.1:9090/api/v1/query?query=$encoded"
            if ($queryResult.status -ne "success" -or @($queryResult.data.result).Count -eq 0) { throw "Dashboard query returned no data: $($panel.title)." }
            Write-Host "PASS: dashboard query returns data: $($panel.title)."
        }
    }
}
$filesystemPanel = $dashboardFile.panels | Where-Object { $_.title -eq "Filesystem Capacity · Logical Volumes" }
if (-not $filesystemPanel) { throw "Filesystem capacity panel is missing." }
if (-not $filesystemPanel.targets[0].instant -or $filesystemPanel.targets[0].range -or $filesystemPanel.options.reduceOptions.values) {
    throw "Filesystem capacity must be an instant query reduced to one value per series."
}
$filesystemExpression = [Uri]::EscapeDataString($filesystemPanel.targets[0].expr)
$filesystemResults = (Get-Json "http://127.0.0.1:9090/api/v1/query?query=$filesystemExpression").data.result
$allowedMounts = @("/mnt/host/c", "/mnt/docker-desktop-disk")
foreach ($series in $filesystemResults) {
    $labels = $series.metric
    $expectedVolume = if ($labels.mountpoint -eq "/mnt/host/c") { "Windows C:" } elseif ($labels.mountpoint -eq "/mnt/docker-desktop-disk") { "Docker virtual disk" } else { $null }
    if ($labels.mountpoint -notin $allowedMounts -or $labels.volume -ne $expectedVolume -or $labels.device -or $labels.fstype -or $labels.instance -or $labels.job) {
        throw "A filtered or irrelevant filesystem appeared in the capacity query: $($labels.mountpoint) ($($labels.fstype), $($labels.device))."
    }
}
$distinctMounts = @($filesystemResults | ForEach-Object { $_.metric.mountpoint } | Sort-Object -Unique)
if ($filesystemResults.Count -ne $distinctMounts.Count) { throw "Filesystem capacity returns more than one series per logical mountpoint." }
Write-Host "PASS: filesystem capacity returns one current series per selected logical volume, with friendly labels."
$targetKpi = $dashboardFile.panels | Where-Object { $_.title -eq "Targets UP / Total" }
if (-not $targetKpi -or @($targetKpi.targets).Count -ne 2) { throw "Combined target KPI is missing its UP and total queries." }
$upExpression = [Uri]::EscapeDataString($targetKpi.targets[0].expr)
$totalExpression = [Uri]::EscapeDataString($targetKpi.targets[1].expr)
$upValue = [double](Get-Json "http://127.0.0.1:9090/api/v1/query?query=$upExpression").data.result[0].value[1]
$totalValue = [double](Get-Json "http://127.0.0.1:9090/api/v1/query?query=$totalExpression").data.result[0].value[1]
if ($totalValue -le 0 -or $upValue -lt 0 -or $upValue -gt $totalValue) { throw "Targets UP / Total returned an invalid fraction: $upValue/$totalValue." }
Write-Host "PASS: live target KPI is $upValue/$totalValue UP / total."
$identity = (Get-Json "http://127.0.0.1:9090/api/v1/query?query=node_uname_info").data.result[0].metric
Write-Host "Observed Linux identity: sysname=$($identity.sysname), release=$($identity.release), machine=$($identity.machine)"
$null = Invoke-RestMethod -Uri "http://127.0.0.1:3000/api/health" -TimeoutSec 10
Write-Host "PASS: Grafana health endpoint responds."
$datasource = Get-Json "http://127.0.0.1:3000/api/datasources/uid/prometheus" -Authenticated
if ($datasource.url -ne "http://prometheus-internal:9090") { throw "Grafana Prometheus datasource URL is unexpected." }
Write-Host "PASS: Grafana datasource is provisioned."
$health = Get-Json "http://127.0.0.1:3000/api/datasources/uid/prometheus/health" -Authenticated
if ($health.status -ne "OK") { throw "Grafana cannot query Prometheus." }
Write-Host "PASS: Grafana can query Prometheus over the Compose network."
$alertmanagerStatus = Get-Json "http://127.0.0.1:9093/api/v2/status"
if (-not $alertmanagerStatus.versionInfo.version) { throw "Alertmanager API status is unavailable." }
Write-Host "PASS: Alertmanager API responds."
$activeAlertmanagerAlerts = Get-Json "http://127.0.0.1:9093/api/v2/alerts?active=true&silenced=false&inhibited=false"
if (@($activeAlertmanagerAlerts | Where-Object { $_.labels.alertname -eq "TestIncident" }).Count -gt 0) { throw "A controlled TestIncident is still active in Alertmanager." }
Write-Host "PASS: no controlled test incident remains active in Alertmanager."
$dashboard = Get-Json "http://127.0.0.1:3000/api/dashboards/uid/it-infra-overview" -Authenticated
if ($dashboard.dashboard.title -ne "IT Infrastructure Overview") { throw "Provisioned Grafana dashboard was not found." }
if (-not $dashboard.meta.provisioned -or $dashboard.meta.provisionedExternalId -ne "infrastructure-overview.json") { throw "Grafana dashboard is not marked as provisioned from the expected file." }
$provisionedPanels = @($dashboard.dashboard.panels)
if ($provisionedPanels.Count -ne @($dashboardFile.panels).Count) { throw "Grafana has $($provisionedPanels.Count) panels, expected $(@($dashboardFile.panels).Count)." }
foreach ($title in @("CPU Usage · Linux Engine", "Memory Utilization · Linux Engine", "Disk Usage · Windows C:", "Network RX", "Network TX", "Targets UP / Total", "CPU Trend · Linux Engine", "Memory Trend · Linux Engine", "Network Throughput", "Filesystem Utilization", "Target Health", "System Uptime", "Node Exporter Build", "Filesystem Capacity · Logical Volumes")) {
    if ($provisionedPanels.title -notcontains $title) { throw "Provisioned dashboard is missing panel: $title." }
}
$targetKpi = $dashboardFile.panels | Where-Object { $_.title -eq "Targets UP / Total" }
if (@($targetKpi.targets).Count -ne 2 -or $provisionedPanels.title -contains "Monitored Targets") { throw "Target UP and total must share one KPI, without a redundant panel." }
$loadedTargetKpi = $provisionedPanels | Where-Object { $_.title -eq "Targets UP / Total" }
if (@($loadedTargetKpi.targets).Count -ne 2) { throw "Grafana did not load both queries for the combined target KPI." }
$loadedFilesystem = $provisionedPanels | Where-Object { $_.title -eq "Filesystem Capacity · Logical Volumes" }
if (-not $loadedFilesystem.targets[0].instant -or $loadedFilesystem.targets[0].range -or $loadedFilesystem.options.reduceOptions.values) { throw "Grafana did not load the deduplicated instant filesystem query." }
$loadedTrends = @($provisionedPanels | Where-Object { $_.type -eq "timeseries" } | Sort-Object { $_.gridPos.x })
if ($loadedTrends.Count -ne 4) { throw "Grafana did not load all four trend panels." }
for ($i = 0; $i -lt $loadedTrends.Count; $i++) {
    $position = $loadedTrends[$i].gridPos
    if ($position.y -ne 5 -or $position.x -ne ($i * 6) -or $position.w -ne 6 -or $position.h -ne 7) { throw "Grafana did not load the optimized trend layout." }
}
Write-Host "PASS: Grafana provisioned the 14-panel dashboard with a combined targets KPI."
