# Arquitectura

## Flujo

~~~mermaid
flowchart LR
  ne["Node Exporter:9100"]
  prom["Prometheus:9090"]
  graf["Grafana:3000"]
  am["Alertmanager:9093"]
  logger["Incident Logger:8080"]
  scrape["itmc-monitoring, internal=true"]
  edge["local-access bridge"]
  browser["Navegador"]
  prom -->|"scrape 15 s"| ne
  prom -->|"localhost:9090 self-scrape"| prom
  graf -->|"prometheus-internal:9090"| prom
  prom -->|"alertas"| am
  am -->|"webhook FIRING / RESOLVED"| logger
  ne -.-> scrape
  prom -.-> scrape
  graf -.-> scrape
  prom -.-> edge
  graf -.-> edge
  browser -->|"127.0.0.1:3000"| graf
  browser -->|"127.0.0.1:9090"| prom
  browser -->|"127.0.0.1:9093"| am
  prom --> pv[("itmc-prometheus-data")]
  graf --> gv[("itmc-grafana-data")]
  am --> av[("itmc-alertmanager-data")]
  logger --> lv[("itmc-incident-log-data")]
~~~

Prometheus obtiene métricas del propio servicio y de Node Exporter, evalúa las reglas de `config/prometheus/rules/` y envía alertas a `alertmanager:9093`. Alertmanager remite todas las notificaciones FIRING y RESOLVED al logger HTTP interno. Grafana usa `http://prometheus-internal:9090` dentro de la red Docker. Solo Grafana, Prometheus y la interfaz web de Alertmanager publican puertos, enlazados a loopback. Para que Docker Desktop pueda hacer esos reenvíos, se conectan a la red bridge local no marcada `internal`; la conexión de Grafana al datasource usa el alias `prometheus-internal` de la red de scraping. Esa red adicional permite salida de red a esos tres servicios. Node Exporter y el logger no se conectan a ella.

`incident-logger` escucha en el puerto 8080 únicamente en `itmc-monitoring`; no tiene mapeo de puertos al host. Su API acepta POST de Alertmanager en `/webhook` y responde a su healthcheck. El logger guarda JSONL persistente con rotación limitada (5 MiB por archivo, cuatro archivos como máximo) y elimina duplicados de notificación con una clave SHA-256 derivada de fingerprint, estado y `startsAt`. Alertmanager conserva su estado durante 120 horas en su propio volumen.

## Persistencia y retención

- itmc-prometheus-data: TSDB con retención de 15 días o 5 GB.
- itmc-grafana-data: base de datos, preferencias y estado local.
- itmc-alertmanager-data: estado de alertas y notificaciones por 120 horas.
- itmc-incident-log-data: hasta 20 MiB de historial JSONL rotado.
- Los archivos de configuración y dashboard se versionan bajo config/ y se montan en solo lectura. Provisioning los vuelve a cargar automáticamente.
- docker compose down conserva datos; docker compose down --volumes los elimina.

Las cuatro alertas de producción vigilan targets configurados, CPU >85 %, memoria >90 % y C: >90 %, con duraciones respectivas de 1 o 5 minutos. El `up == 0` no alerta sobre un target eliminado de la configuración: sin una serie scrapeada no existe una muestra cero que evaluar. La regla `TestIncident` se carga desde un archivo separado y queda inactiva con `vector(0) == 1`; el simulador la cambia temporalmente y la restaura incluso si falla.

## Identidad del host observado

Node Exporter monta /proc, /sys y / del host Linux del daemon en modo lectura y configura las rutas de colectores. No se activa PID host porque las métricas mostradas no requieren enumerar procesos.

En esta ejecución node_uname_info informó Linux, kernel 6.18.54.1-microsoft-standard-WSL2 y x86_64. El filesystem /mnt/host/c apareció como 9p y su espacio libre coincidió con el volumen C: de Windows al momento de comprobarlo. CPU, memoria, interfaces y uptime representan el Linux WSL2 del Docker Engine; no son métricas de CPU/memoria de Windows ni de la distro Ubuntu.

Los montajes de raíz permiten leer detalles del filesystem y son sensibles aunque sean de solo lectura. No se monta socket Docker ni se añaden capacidades.

## Red y controles

Red bridge dedicada itmc-monitoring con internal=true; Node Exporter solo es accesible dentro de esa red. Prometheus y Grafana se conectan también a local-access para que Docker Desktop reenvíe sus puertos a 127.0.0.1. Esta red brinda salida a Internet a esos dos servicios; el reporte, las consultas de actualización y los reportes de plugin de Grafana están desactivados. Credenciales en .env, ignorado por Git. no-new-privileges, cap_drop: ALL, sistema de archivos de solo lectura donde aplica y healthchecks reducen privilegios y facilitan verificar disponibilidad.

Los tags de imagen fijan las versiones, pero no digests. Esto es suficiente para esta fase local; puede añadirse pinning de digest con revisión posterior. No se configura TLS, autenticación propia para Prometheus ni aislamiento de producción.
