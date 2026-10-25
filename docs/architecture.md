# Arquitectura

## Flujo

~~~mermaid
flowchart LR
  ne["Node Exporter:9100"]
  prom["Prometheus:9090"]
  graf["Grafana:3000"]
  am["Alertmanager:9093"]
  logger["Incident Logger:8080"]
  exporter["Container Exporter Python\nWindows loopback :9464"]
  dockercli["Docker CLI / desktop-linux"]
  scrape["itmc-monitoring, internal=true"]
  edge["local-access bridge"]
  browser["Navegador"]
  prom -->|"scrape 15 s"| ne
  prom -->|"localhost:9090 self-scrape"| prom
  prom -->|"scrape 15 s: host.docker.internal"| exporter
  exporter -->|"ps + stats read-only"| dockercli
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

La vista completa de arquitectura y los diagramas Mermaid separados de [flujo de métricas, incidentes y CI](diagrams.md) resumen las rutas y el alcance de cada proceso.

`incident-logger` escucha en el puerto 8080 únicamente en `itmc-monitoring`; no tiene mapeo de puertos al host. Su API acepta POST de Alertmanager en `/webhook` y responde a su healthcheck. El logger guarda JSONL persistente con rotación limitada (5 MiB por archivo, cuatro archivos como máximo) y elimina duplicados de notificación con una clave SHA-256 derivada de fingerprint, estado y `startsAt`. Alertmanager conserva su estado durante 120 horas en su propio volumen.

El exporter Python es un proceso local de Windows, fuera de Compose. Prometheus lo scrapea por `host.docker.internal:9464`; el servidor del proceso solo se enlaza a `127.0.0.1`. Se comprobó que Docker Desktop proxifica la conexión de Prometheus hacia un listener loopback y que Windows recibe el peer como `127.0.0.1`; no se abrió un listener LAN ni se alteró firewall. El exporter ejecuta únicamente `docker ps` con el filtro de etiqueta del proyecto y `docker stats --no-stream` para los IDs resultantes. No se monta socket Docker en contenedores.

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

En Docker Desktop/WSL2, node_uname_info identifica Linux/WSL2. El filesystem /mnt/host/c puede aparecer como 9p y reflejar el espacio disponible del volumen C: compartido. CPU, memoria, interfaces y uptime representan el Linux del Docker Engine; no son métricas de CPU/memoria de Windows ni de una distro Ubuntu separada.

Los montajes de raíz de Node Exporter permiten leer detalles del filesystem y son sensibles aunque sean de solo lectura. No se monta socket Docker dentro de contenedores. El proceso host del exporter sí hereda el acceso del usuario a Docker Desktop; aunque el código use únicamente operaciones de lectura, ese usuario conserva facultad significativa sobre el daemon. Usa este proceso solo con código confiable.

## Fase 3A: cAdvisor no desplegado; alternativa 3B activa

La ruta cAdvisor se detuvo antes de cambiar la arquitectura. En Docker Desktop/WSL2, las series individuales identificables y su tráfico de red solo aparecieron con acceso a los sockets Docker y containerd, además de montajes del host. No se desplegó cAdvisor. La alternativa Fase 3B es el proceso Python host-side descrito arriba; consulta [la evaluación cAdvisor](container-monitoring-assessment.md) y [el diseño y límites del exporter](container-exporter.md).

## Red y controles

Red bridge dedicada itmc-monitoring con internal=true; Node Exporter solo es accesible dentro de esa red. Prometheus y Grafana se conectan también a local-access para que Docker Desktop reenvíe sus puertos a 127.0.0.1. Esta red brinda salida a Internet a esos dos servicios; el reporte, las consultas de actualización y los reportes de plugin de Grafana están desactivados. Credenciales en .env, ignorado por Git. no-new-privileges, cap_drop: ALL, sistema de archivos de solo lectura donde aplica y healthchecks reducen privilegios y facilitan verificar disponibilidad.

Los tags de imagen fijan las versiones, pero no digests. Esto es suficiente para esta fase local; puede añadirse pinning de digest con revisión posterior. No se configura TLS, autenticación propia para Prometheus ni aislamiento de producción.

## Integración continua

GitHub Actions valida sintaxis y pruebas Python, analiza scripts PowerShell sin ejecutarlos, revisa el JSON provisionado y verifica configuraciones/reglas con versiones de promtool y amtool que coinciden con Prometheus y Alertmanager del Compose. `docker compose config` se ejecuta contra una copia temporal con un `.env` de prueba; ningún servicio ni volumen se inicia o modifica. El workflow solo requiere permiso `contents: read`, no contiene secretos y no ejecuta el exporter contra el daemon Linux del runner. Las comprobaciones vivas de Docker Desktop/WSL2, targets, datasource y consultas requieren los comandos locales descritos en [docs/ci.md](ci.md).

## Home Network Monitoring — preparada en `feature/home-network-monitoring`

La rama añade Blackbox Exporter 0.28.0 y una red `home-probes` dedicada a Prometheus y al exporter. Esa red bridge permite salida hacia los destinos aprobados; el exporter no publica puertos, no recibe socket Docker, descarta capabilities y prueba ICMP con un rango de ping socket limitado. Los destinos externos configurados son `1.1.1.1` (ICMP y DNS por UDP/53) y `example.com` (HTTPS, sin seguir redirecciones). El router doméstico se especifica solo en un archivo local ignorado por Git.

La integración está activa en el entorno local validado: el scrape del exporter y los cinco probes devolvieron muestras recientes exitosas. Este resultado depende de la conectividad particular de Docker Desktop/WSL2 y no garantiza historial continuo. Ver [arquitectura/seguridad y activación doméstica](home-network-monitoring.md), [runbook](runbooks/home-network.md) e [informe redactado de preparación](incidents/blackbox-exporter-preparation.md).
