# Resolución de problemas

## Docker Desktop no responde

~~~powershell
docker context show
docker info
wsl --list --verbose
~~~

Abre Docker Desktop y espera a que el motor Linux esté listo. No cambies contexto ni ajustes globales sin entender el impacto en otros proyectos.

## Falta .env

~~~powershell
Copy-Item .env.example .env
notepad .env
~~~

Reemplaza REPLACE_WITH_A_LONG_RANDOM_PASSWORD. No publiques .env.

## Puerto ocupado

~~~powershell
Get-NetTCPConnection -State Listen -LocalPort 3000,9090,9093 -ErrorAction SilentlyContinue
~~~

Identifica el proceso antes de actuar. No detengas ni elimines servicios de otro proyecto.

## Servicios no saludables

~~~powershell
docker compose ps
docker compose logs --tail=100 prometheus
docker compose logs --tail=100 grafana
docker compose logs --tail=100 alertmanager
docker compose logs --tail=100 incident-logger
Invoke-RestMethod http://127.0.0.1:9090/-/healthy
Invoke-RestMethod http://127.0.0.1:3000/api/health
Invoke-RestMethod http://127.0.0.1:9093/-/healthy
~~~

La inicialización de Grafana puede tardar unos segundos; el healthcheck permite 30 segundos de inicio.

## Node Exporter está DOWN

~~~powershell
docker compose logs --tail=100 node-exporter
docker compose exec prometheus wget -qO- http://node-exporter:9100/metrics
~~~

Los montajes de /proc, /sys y / dependen del daemon. En Docker Desktop compara node_uname_info y node_filesystem_size_bytes con la VM Linux; no asumas que CPU, memoria o uptime describen Windows o Ubuntu WSL. El montaje /mnt/host/c puede reportar espacio libre de C: mediante 9p.

## Dashboard no aparece

~~~powershell
docker compose exec grafana ls -la /var/lib/grafana/dashboards
docker compose logs --tail=100 grafana
~~~

El proveedor revisa archivos cada 30 segundos. El datasource se define en config/grafana/provisioning/datasources/prometheus.yml.

## Alertas o webhook no llegan

~~~powershell
docker compose ps
docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose exec -T prometheus promtool check rules /etc/prometheus/rules/infrastructure.yml /etc/prometheus/test-rules/incident-test.yml
docker compose exec -T alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
docker compose logs --tail=100 alertmanager incident-logger
docker compose exec -T incident-logger python /app/incident_logger.py --tail 20
~~~

Prometheus debe tener cinco reglas alerting cargadas: cuatro de producción y `TestIncident` inactiva. La ruta de Alertmanager usa `group_wait: 3s` y `group_interval: 5s`; una alerta puede aparecer como FIRING antes de que llegue su webhook. El simulador mide ambos tiempos por separado. `TargetDown` solo detecta un target que sigue configurado y responde DOWN; quitar el target de `prometheus.yml` elimina su serie y no produce `up == 0`.

Ejecuta `scripts/simulate-incident.ps1` o `bash scripts/simulate-incident.sh` para forzar un ciclo controlado sin consumir recursos. El simulador restaura la regla de prueba en un bloque `finally`. Los reportes que genera se guardan en `data/` y el historial persiste en `itmc-incident-log-data`.

## Persistencia

~~~powershell
docker compose restart
Start-Sleep -Seconds 30
docker compose ps
~~~

Consulta un rango que incluya datos previos. No ejecutes docker compose down --volumes.
