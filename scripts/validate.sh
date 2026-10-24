#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
command -v docker >/dev/null || { echo "ERROR: docker CLI is required." >&2; exit 2; }
command -v python3 >/dev/null || { echo "ERROR: python3 is required." >&2; exit 2; }
[[ -f .env ]] || { echo "ERROR: copy .env.example to .env and set credentials." >&2; exit 2; }
docker compose config --quiet
echo "PASS: Docker Compose configuration is valid."
docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
echo "PASS: Prometheus config is valid."
docker compose exec -T prometheus promtool check rules /etc/prometheus/rules/infrastructure.yml /etc/prometheus/test-rules/incident-test.yml
echo "PASS: Prometheus alert rules are valid."
docker compose exec -T prometheus promtool test rules /etc/prometheus/tests/infrastructure.test.yml
echo "PASS: Prometheus alert rule tests passed."
docker compose exec -T alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
echo "PASS: Alertmanager config is valid."
python3 -m unittest discover -s services/incident-logger -p test_incident_logger.py
echo "PASS: incident logger webhook unit tests passed."
docker compose exec -T incident-logger python /app/incident_logger.py --healthcheck
echo "PASS: incident logger health endpoint responds on its internal Docker network."
alertmanager_port="$(docker compose port alertmanager 9093)"
[[ "$alertmanager_port" == "127.0.0.1:9093" ]] || { echo "ERROR: Alertmanager UI must be published only on 127.0.0.1:9093." >&2; exit 1; }
logger_container="$(docker compose ps -q incident-logger)"
logger_bindings="$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$logger_container")"
[[ "$logger_bindings" == "{}" || "$logger_bindings" == "null" ]] || { echo "ERROR: incident logger must not publish a host port." >&2; exit 1; }
echo "PASS: Alertmanager is loopback-only and incident logger has no published port."

python3 - <<'PY'
import base64, json, sys, urllib.parse, urllib.request
from pathlib import Path

def fail(message):
    print("FAIL: " + message, file=sys.stderr)
    sys.exit(1)

env = {}
for raw in Path(".env").read_text(encoding="utf-8-sig").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key.strip()] = value
user, password = env.get("GF_SECURITY_ADMIN_USER"), env.get("GF_SECURITY_ADMIN_PASSWORD")
if not user or not password or password == "REPLACE_WITH_A_LONG_RANDOM_PASSWORD":
    fail("set local Grafana credentials in .env")

def get(url, auth=False):
    headers = {}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode((user + ":" + password).encode()).decode()
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode()
            return json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body
    except Exception as exc:
        fail("GET " + url + " failed: " + str(exc))

get("http://127.0.0.1:9090/-/healthy")
print("PASS: Prometheus availability endpoint responds.")
get("http://127.0.0.1:9093/-/healthy")
print("PASS: Alertmanager availability endpoint responds.")
targets = get("http://127.0.0.1:9090/api/v1/targets").get("data", {}).get("activeTargets", [])
for job in ("prometheus", "node-exporter"):
    found = [t for t in targets if t.get("labels", {}).get("job") == job]
    if not found or any(t.get("health") != "up" for t in found):
        fail("Prometheus target " + job + " is missing or not UP")
    print("PASS: Prometheus target " + job + " is UP.")

def query(expr):
    url = "http://127.0.0.1:9090/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    result = get(url)
    series = result.get("data", {}).get("result", [])
    if result.get("status") != "success" or not series:
        fail("query returned no data: " + expr)
    return series

for metric in ("node_uname_info", "node_cpu_seconds_total", "node_memory_MemTotal_bytes", "node_filesystem_avail_bytes", "node_network_receive_bytes_total", "node_boot_time_seconds"):
    query(metric)
    print("PASS: real Node Exporter metric is available: " + metric)
rule_response = get("http://127.0.0.1:9090/api/v1/rules?type=alert")
alert_rules = [rule for group in rule_response.get("data", {}).get("groups", []) for rule in group.get("rules", []) if rule.get("type") == "alerting"]
expected_alerts = {"TargetDown", "HighCPUUsage", "HighMemoryUsage", "DiskSpaceCritical", "TestIncident"}
loaded_alerts = {rule.get("name") for rule in alert_rules}
if not expected_alerts.issubset(loaded_alerts):
    fail("Prometheus has not loaded all expected alert rules: " + ", ".join(sorted(expected_alerts - loaded_alerts)))
test_rule = next(rule for rule in alert_rules if rule.get("name") == "TestIncident")
if test_rule.get("query") != "vector(0) == 1" or test_rule.get("alerts"):
    fail("TestIncident must remain disabled outside the controlled test")
print("PASS: four production alert rules are loaded and TestIncident is disabled.")
dashboard_file = json.loads(Path("config/grafana/dashboards/infrastructure-overview.json").read_text(encoding="utf-8-sig"))
trend_panels = sorted((panel for panel in dashboard_file.get("panels", []) if panel.get("type") == "timeseries"), key=lambda panel: panel.get("gridPos", {}).get("x", -1))
if len(trend_panels) != 4:
    fail("expected four trend panels on the main trend row")
for index, panel in enumerate(trend_panels):
    position = panel.get("gridPos", {})
    if position.get("y") != 5 or position.get("x") != index * 6 or position.get("w") != 6 or position.get("h") != 7:
        fail("trend panels are not arranged as four balanced columns for a 1920x1080 view")
print("PASS: four trend panels share a balanced row for 1920x1080.")
for panel in dashboard_file.get("panels", []):
    for target in panel.get("targets", []):
        expr = target.get("expr")
        if expr:
            query(expr.replace("$__rate_interval", "1m"))
            print("PASS: dashboard query returns data: " + panel.get("title", "unnamed panel"))
filesystem_panel = next((panel for panel in dashboard_file.get("panels", []) if panel.get("title") == "Filesystem Capacity · Logical Volumes"), None)
if not filesystem_panel:
    fail("filesystem capacity panel is missing")
filesystem_target = filesystem_panel["targets"][0]
if not filesystem_target.get("instant") or filesystem_target.get("range") or filesystem_panel.get("options", {}).get("reduceOptions", {}).get("values"):
    fail("filesystem capacity must be an instant query reduced to one value per series")
filesystem_series = query(filesystem_panel["targets"][0]["expr"])
allowed_mounts = {"/mnt/host/c", "/mnt/docker-desktop-disk"}
mountpoints = []
for series in filesystem_series:
    labels = series.get("metric", {})
    expected_volume = {"/mnt/host/c": "Windows C:", "/mnt/docker-desktop-disk": "Docker virtual disk"}.get(labels.get("mountpoint"))
    if labels.get("mountpoint") not in allowed_mounts or labels.get("volume") != expected_volume or any(labels.get(key) for key in ("device", "fstype", "instance", "job")):
        fail("filtered or irrelevant filesystem appeared in capacity query: " + str(labels))
    mountpoints.append(labels["mountpoint"])
if len(mountpoints) != len(set(mountpoints)):
    fail("filesystem capacity returns more than one series per logical mountpoint")
print("PASS: filesystem capacity returns one current series per selected logical volume, with friendly labels.")
target_kpi = next((panel for panel in dashboard_file.get("panels", []) if panel.get("title") == "Targets UP / Total"), None)
if not target_kpi or len(target_kpi.get("targets", [])) != 2:
    fail("combined target KPI is missing its UP and total queries")
up_value = float(query(target_kpi["targets"][0]["expr"])[0]["value"][1])
total_value = float(query(target_kpi["targets"][1]["expr"])[0]["value"][1])
if total_value <= 0 or up_value < 0 or up_value > total_value:
    fail("target KPI returned an invalid fraction: {0}/{1}".format(up_value, total_value))
print("PASS: live target KPI is {0:g}/{1:g} UP / total.".format(up_value, total_value))
identity = query("node_uname_info")[0].get("metric", {})
print("Observed Linux identity: sysname={0}, release={1}, machine={2}".format(identity.get("sysname", "?"), identity.get("release", "?"), identity.get("machine", "?")))

get("http://127.0.0.1:3000/api/health")
print("PASS: Grafana health endpoint responds.")
alertmanager_status = get("http://127.0.0.1:9093/api/v2/status")
if not alertmanager_status.get("versionInfo", {}).get("version"):
    fail("Alertmanager API status is unavailable")
print("PASS: Alertmanager API responds.")
active_alerts = get("http://127.0.0.1:9093/api/v2/alerts?active=true&silenced=false&inhibited=false")
if any(alert.get("labels", {}).get("alertname") == "TestIncident" for alert in active_alerts):
    fail("a controlled TestIncident is still active in Alertmanager")
print("PASS: no controlled test incident remains active in Alertmanager.")
ds = get("http://127.0.0.1:3000/api/datasources/uid/prometheus", True)
if ds.get("url") != "http://prometheus-internal:9090":
    fail("Grafana Prometheus datasource URL is unexpected")
print("PASS: Grafana datasource is provisioned.")
health = get("http://127.0.0.1:3000/api/datasources/uid/prometheus/health", True)
if health.get("status") != "OK":
    fail("Grafana cannot query Prometheus: " + str(health))
print("PASS: Grafana can query Prometheus over the Compose network.")
dashboard = get("http://127.0.0.1:3000/api/dashboards/uid/it-infra-overview", True)
if dashboard.get("dashboard", {}).get("title") != "IT Infrastructure Overview":
    fail("provisioned Grafana dashboard was not found")
dashboard_meta = dashboard.get("meta", {})
if not dashboard_meta.get("provisioned") or dashboard_meta.get("provisionedExternalId") != "infrastructure-overview.json":
    fail("Grafana dashboard is not marked as provisioned from the expected file")
provisioned_panels = dashboard.get("dashboard", {}).get("panels", [])
if len(provisioned_panels) != len(dashboard_file.get("panels", [])):
    fail("Grafana has {0} panels, expected {1}".format(len(provisioned_panels), len(dashboard_file.get("panels", []))))
expected_titles = {"CPU Usage · Linux Engine", "Memory Utilization · Linux Engine", "Disk Usage · Windows C:", "Network RX", "Network TX", "Targets UP / Total", "CPU Trend · Linux Engine", "Memory Trend · Linux Engine", "Network Throughput", "Filesystem Utilization", "Target Health", "System Uptime", "Node Exporter Build", "Filesystem Capacity · Logical Volumes"}
provisioned_titles = {panel.get("title") for panel in provisioned_panels}
missing_titles = expected_titles - provisioned_titles
if missing_titles:
    fail("provisioned dashboard is missing panels: " + ", ".join(sorted(missing_titles)))
if len(target_kpi.get("targets", [])) != 2 or "Monitored Targets" in provisioned_titles:
    fail("target UP and total must share one KPI, without a redundant panel")
loaded_target_kpi = next((panel for panel in provisioned_panels if panel.get("title") == "Targets UP / Total"), None)
loaded_filesystem = next((panel for panel in provisioned_panels if panel.get("title") == "Filesystem Capacity · Logical Volumes"), None)
if not loaded_target_kpi or len(loaded_target_kpi.get("targets", [])) != 2:
    fail("Grafana did not load both queries for the combined target KPI")
if not loaded_filesystem or not loaded_filesystem["targets"][0].get("instant") or loaded_filesystem["targets"][0].get("range") or loaded_filesystem.get("options", {}).get("reduceOptions", {}).get("values"):
    fail("Grafana did not load the deduplicated instant filesystem query")
loaded_trends = sorted((panel for panel in provisioned_panels if panel.get("type") == "timeseries"), key=lambda panel: panel.get("gridPos", {}).get("x", -1))
if len(loaded_trends) != 4:
    fail("Grafana did not load all four trend panels")
for index, panel in enumerate(loaded_trends):
    position = panel.get("gridPos", {})
    if position.get("y") != 5 or position.get("x") != index * 6 or position.get("w") != 6 or position.get("h") != 7:
        fail("Grafana did not load the optimized trend layout")
print("PASS: Grafana provisioned 14 panels, the combined target KPI, and the optimized layouts.")
PY
