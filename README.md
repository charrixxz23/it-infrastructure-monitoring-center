# IT Infrastructure Monitoring Center

Centro local de monitoreo con Prometheus, Grafana, Node Exporter y Alertmanager. Reúne métricas Linux en un dashboard provisionado automáticamente, evalúa alertas y conserva un historial local de incidentes. Incluye un exporter Python opcional en Windows que consulta Docker CLI con el filtro de proyecto y escucha solo en loopback.

> English: A reproducible local infrastructure monitoring lab built with Prometheus, Grafana, Node Exporter, Alertmanager, and Docker Compose. It provisions a dashboard, evaluates alerts, records local incidents, and persists state.

## Problema que resuelve

Sin observabilidad resulta difícil identificar saturación de CPU, memoria, disco o red y demostrar la salud de los servicios. Esta fase centraliza métricas reales y ofrece un panel inicial listo para usar. Está pensada para desarrollo local y portafolio, no para producción.

## Arquitectura

~~~mermaid
flowchart LR
    ne["Node Exporter :9100"]
    prom["Prometheus :9090"]
    graf["Grafana :3000"]
    am["Alertmanager :9093"]
    logger["Incident Logger :8080"]
    scrape["Red interna itmc-monitoring"]
    edge["Red local-access"]
    browser["Navegador local"]
    exp["Exporter Python en Windows\n127.0.0.1:9464"]
    engine["Docker CLI → Docker Desktop"]
    prom -->|"scrape 15 s"| ne
    prom -->|"autocolección"| prom
    prom -->|"scrape 15 s vía host.docker.internal"| exp
    exp -->|"docker ps + stats --no-stream"| engine
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
    prom --- pv[("itmc-prometheus-data")]
    graf --- gv[("itmc-grafana-data")]
    am --- av[("itmc-alertmanager-data")]
    logger --- lv[("itmc-incident-log-data")]
~~~

Node Exporter lee los pseudo-sistemas de archivos Linux que expone el daemon. En esta ejecución node_uname_info informó el kernel 6.18.54.1-microsoft-standard-WSL2 y la máquina x86_64. El disco de Docker Desktop también expuso /mnt/host/c como 9p; su espacio disponible coincidió con el volumen C: de Windows al momento de verificarlo. CPU, memoria, red y uptime corresponden al entorno Linux WSL2 del Docker Engine, no a Windows físico ni a Ubuntu WSL.

## Stack y versiones

| Componente | Versión fijada | Función |
|---|---:|---|
| Docker CLI observado | 29.9.2 | Cliente del motor |
| Docker Compose observado | v5.6.1 | Orquestación |
| Prometheus | 3.15.0 | Scraping y TSDB |
| Grafana | 13.2.4 | Visualización |
| Node Exporter | 1.12.1 | Métricas del host Linux visible al daemon |
| Alertmanager | 0.34.1 | Agrupación y entrega de alertas |
| Incident Logger | Python 3.13 (stdlib) | Receptor webhook e historial JSONL local |
| Container Exporter | Python 3.10+ (stdlib, proceso host opcional) | Snapshot de CPU, memoria y red solo del proyecto Compose |

Referencias oficiales: [Prometheus](https://github.com/prometheus/prometheus/releases), [Grafana](https://github.com/grafana/grafana/releases), [Node Exporter](https://github.com/prometheus/node_exporter/releases), [Alertmanager](https://github.com/prometheus/alertmanager/releases).

## Requisitos

- Windows 11 x64, WSL2 y Docker Desktop con backend Linux.
- Docker Compose v2 o posterior, navegador y puertos locales 3000, 9090 y 9093 libres; 9464 debe estar libre para el exporter opcional.
- Memoria y disco disponibles en Docker Desktop. Retención: 15 días o 5 GB, lo que ocurra primero.

Abre Docker Desktop antes de usar el laboratorio y confirma que el contexto Docker sea desktop-linux.

## Instalación

Copia .env.example a .env y sustituye el marcador de contraseña por una contraseña larga aleatoria. Nunca publiques .env.

La ruta cAdvisor de Fase 3A se detuvo por seguridad: en Docker Desktop/WSL2 requería acceso a los sockets Docker y containerd. La alternativa Fase 3B usa un exporter Python local de loopback, sin montar sockets en contenedores; requiere ejecutarlo manualmente para que el dashboard Docker muestre datos. Consulta [la evaluación cAdvisor](docs/container-monitoring-assessment.md) y [el diseño del exporter](docs/container-exporter.md).

PowerShell desde la raíz:

~~~powershell
Copy-Item .env.example .env
notepad .env
docker compose version
docker compose config --quiet
docker compose up -d
docker compose ps
~~~

Linux/WSL:

~~~bash
cp .env.example .env
# Edita .env y define una contraseña propia.
docker compose version
docker compose config --quiet
docker compose up -d
docker compose ps
~~~

Para habilitar el dashboard opcional de contenedores, espera a que Docker Desktop esté listo y ejecuta `docker context show` (`desktop-linux`) y `docker info`. Después abre una ventana de PowerShell en la raíz del repositorio y ejecuta `.\scripts\run-container-exporter.ps1`. El proceso foreground de Windows escucha en `127.0.0.1:9464`; pulsa `Ctrl+C` para detenerlo y vuelve a ejecutar el comando para reiniciarlo. No se inicia automáticamente tras cerrar la ventana o reiniciar Windows; el proyecto no instala tareas programadas ni servicios. Prometheus llega al listener por `host.docker.internal`. El proceso conserva acceso significativo al motor Docker como el usuario actual; lee solo etiquetas Compose y `docker stats`, pero no es una frontera de privilegios. Detalles en [docs/container-exporter.md](docs/container-exporter.md).

## Acceso y dashboard

- Grafana: [http://127.0.0.1:3000](http://127.0.0.1:3000). Credenciales definidas en .env.
- Dashboard provisionado: **IT Infrastructure Overview**, carpeta **Infrastructure**.
- Dashboard provisionado independiente: **Docker Container Monitoring** (requiere que el exporter local esté ejecutándose para mostrar métricas recientes).
- Prometheus: [http://127.0.0.1:9090](http://127.0.0.1:9090).
- Alertmanager: [http://127.0.0.1:9093](http://127.0.0.1:9093), interfaz de operación local.
- Salud: [http://127.0.0.1:9090/-/healthy](http://127.0.0.1:9090/-/healthy).
- Targets: [http://127.0.0.1:9090/targets](http://127.0.0.1:9090/targets).

El dashboard tiene 14 paneles en cuatro secciones: seis KPIs (CPU y memoria del Linux Engine, disco C:, RX/TX y targets UP/total), cuatro tendencias en una sola fila, tres indicadores de estado/identidad y capacidad de almacenamiento. Está distribuido para aprovechar una pantalla 1920x1080, se actualiza cada 15 segundos y abre las últimas seis horas.

Las consultas principales usan métricas reales de Node Exporter:

- **CPU:** `100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{job="node-exporter",mode="idle"}[$__rate_interval])))`.
- **Memoria:** `100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)`.
- **Filesystem usado:** `max by (mountpoint) (100 * (1 - node_filesystem_avail_bytes / node_filesystem_size_bytes))`; `label_replace` nombra los volúmenes Windows C: y Docker virtual disk.
- **Red:** `rate(node_network_receive_bytes_total[$__rate_interval])` y su equivalente `node_network_transmit_bytes_total`.
- **Targets UP / Total:** dos consultas en un mismo KPI: `sum(up{job=~"prometheus|node-exporter"})` y `count(up{job=~"prometheus|node-exporter"})`. Este KPI conserva el alcance del dashboard de infraestructura (2/2); el exporter opcional tiene su propio indicador en Docker Container Monitoring. El panel Target Health conserva el estado individual de cada servicio.
- **Uptime:** `time() - node_boot_time_seconds`.

Los paneles de capacidad se limitan a `/mnt/host/c` y `/mnt/docker-desktop-disk`, excluyen `/dev/loop*`, tmpfs, overlay, squashfs, proc, sysfs, cgroup, nsfs y devtmpfs, y muestran una sola serie por `mountpoint`. La consulta agrupa con `max by (mountpoint)` para descartar dimensiones de etiqueta `device`, `fstype`, `instance` y `job`; si esas etiquetas contienen observaciones repetidas del mismo montaje, `max` conserva el mayor uso observado para no subestimar presión de capacidad. El bar gauge es una consulta instantánea y calcula el último valor, en vez de mostrar cada muestra del rango temporal. Los nombres visibles son **Windows C:** para `/mnt/host/c` (9p) y **Docker virtual disk** para `/mnt/docker-desktop-disk` (ext4). La tarjeta principal de disco usa C:. CPU y memoria llevan la etiqueta **Linux Engine**. En Docker Desktop/WSL2, CPU, RAM, red y uptime describen el motor Linux, no Windows físico ni Ubuntu WSL.

CPU, memoria y disco se muestran en `%`; la red usa bytes/s IEC (KiB/s o MiB/s según el tráfico), y uptime usa duración legible. Los umbrales de CPU son 70/85 %, memoria 75/90 % y C: 75/90 %; la vista de capacidad usa 70/85 %. El estado por target muestra UP en verde y DOWN en rojo.

## Alertas e historial de incidentes

Prometheus carga las reglas versionadas desde `config/prometheus/rules/` y remite las alertas a Alertmanager dentro de la red `itmc-monitoring`. Alertmanager se guarda en el volumen `itmc-alertmanager-data` (retención de 120 horas) y su interfaz está disponible solo en [http://127.0.0.1:9093](http://127.0.0.1:9093). El logger recibe webhooks únicamente dentro de la red Docker, no publica puertos al host y guarda eventos JSONL en `itmc-incident-log-data`; cada archivo tiene un límite de 5 MiB y se conservan hasta tres archivos rotados además del actual.

Reglas incluidas: `TargetDown` (un target configurado permanece DOWN 1 minuto, critical), `HighCPUUsage` (>85 % durante 5 minutos, warning), `HighMemoryUsage` (>90 % durante 5 minutos, warning) y `DiskSpaceCritical` (uso de `/mnt/host/c` >90 % durante 5 minutos, critical). CPU, RAM y uptime describen el Linux Engine/WSL2, no Windows físico. `up == 0` detecta targets configurados que fallan; no detecta un target eliminado de la configuración porque esa serie deja de existir.

La deduplicación del logger usa `SHA-256(fingerprint + estado + startsAt)`: reintentos de la misma notificación se reconocen y un evento RESOLVED se registra por separado. El receptor guarda el estado FIRING/RESOLVED, severidad, fingerprint, etiquetas de contexto permitidas, anotaciones y marcas de tiempo. Como es un laboratorio local sin TLS ni autenticación de producción, no lo expongas fuera de Docker.

`TestIncident` está desactivada normalmente (`vector(0) == 1`). `scripts/simulate-incident.ps1` y `scripts/simulate-incident.sh` la habilitan y deshabilitan temporalmente en el archivo dedicado `config/prometheus/test-rules/incident-test.yml`, recargan Prometheus y comprueban el ciclo completo por APIs y el registro real recibido vía Alertmanager. La regla se restaura con `try/finally`; los informes se escriben bajo `data/` (ignorado por Git). El simulador envía SIGHUP al proceso Prometheus mediante Compose y espera el contador de recarga exitosa, sin habilitar el endpoint HTTP de reload ni detener el servicio.

Para comprobar que el dashboard y todas las consultas provisionadas siguen activos:

~~~powershell
.\scripts\validate.ps1
~~~

Con el exporter en ejecución, valida también su acceso al motor y el dashboard independiente:

~~~powershell
.\scripts\validate-container-exporter.ps1
~~~

Para ejecutar validaciones de reglas y probar FIRING → RESOLVED sin generar carga real:

~~~powershell
docker compose up -d --build
.\scripts\validate.ps1
.\scripts\simulate-incident.ps1
~~~

En WSL/Bash ejecuta `bash scripts/validate.sh` y `bash scripts/simulate-incident.sh`. El validador ejecuta `promtool check config`, `promtool check rules`, `promtool test rules` y `amtool check-config`, además de revisar servicios, API, datasource y dashboard. El simulador registra los tiempos observados por Prometheus, Alertmanager y el webhook; separa el momento en que Prometheus pasa a FIRING del momento en que llega la notificación.

## Operación

~~~powershell
docker compose ps
docker compose logs --tail=50
docker compose logs -f prometheus
docker compose logs -f grafana
docker compose logs -f alertmanager incident-logger
docker compose exec -T incident-logger python /app/incident_logger.py --tail 20
docker compose restart
docker compose down
~~~

Los volúmenes nombrados administrados por Compose persisten tras reiniciar y usar docker compose down. No uses docker compose down --volumes si quieres conservarlos.

## Comprobaciones

Con los servicios ya iniciados:

~~~bash
bash scripts/validate.sh
~~~

O desde PowerShell:

~~~powershell
.\scripts\validate.ps1
~~~

Los scripts consultan endpoints y verifican targets, métricas, datasource y dashboard. Para verificar persistencia, consulta la serie up en Prometheus, ejecuta docker compose restart, espera 30 segundos y consulta el historial y Grafana de nuevo. No es necesario borrar volúmenes.

El estado real de este checkout está en [docs/verification-results.md](docs/verification-results.md). Separa validaciones estáticas de pruebas que requieren Docker activo.

## Seguridad

- Grafana, Prometheus y la interfaz web de Alertmanager solo publican en 127.0.0.1. Node Exporter no publica el puerto 9100; incident-logger tampoco publica puerto alguno.
- Node Exporter e incident-logger permanecen solo en la red interna de scraping. Grafana, Prometheus y Alertmanager también se conectan a la red local de acceso porque Docker Desktop no publica puertos desde una red marcada internal. La red adicional permite salida de red a estos tres servicios, aunque las comprobaciones de actualizaciones y reportes de Grafana están desactivadas.
- .env se ignora en Git y .env.example solo tiene un marcador de contraseña.
- No se monta socket Docker, no se usa privileged ni namespace PID del host.
- El exporter Docker no es contenedor: es un proceso del usuario en Windows, con acceso Docker significativo. Ejecuta solo desde código confiable. Su servidor se limita a `127.0.0.1`; el camino de Docker Desktop proxyado a loopback permite scraping desde contenedores y no identifica el contenedor cliente. Expone únicamente estadísticas acotadas del proyecto, sin nombres completos ni variables de entorno. Revisa [la evaluación de seguridad](docs/container-exporter.md).
- Node Exporter monta /proc, /sys y / del host Linux en solo lectura. Esto expone datos sensibles del kernel/filesystem al exporter; revisa los permisos antes de ejecutar en otra máquina. La red local-access da salida de red a Prometheus, Grafana y Alertmanager; los chequeos de actualizaciones y reportes de Grafana están desactivados.
- Prometheus no tiene autenticación en esta fase. Mantén su puerto en loopback.
- Laboratorio local, sin TLS ni endurecimiento de producción.

## Docker Desktop y WSL2

Docker Desktop ejecuta el daemon Linux en una VM. Los bind mounts se resuelven en el contexto de ese daemon. Las métricas Node Exporter y Docker stats se deben describir como las del motor Linux, no Windows físico ni automáticamente Ubuntu en WSL. El exporter validó `host.docker.internal` desde Prometheus mientras escucha solo en loopback; Docker Desktop proxifica esa conexión al host. No se agregó una regla de firewall ni se abrió una interfaz LAN. Para Windows físico se necesitaría Windows Exporter.

## Problemas comunes

- **Daemon inaccesible:** abre Docker Desktop, espera el backend y comprueba el contexto y docker info.
- **Falta .env:** copia .env.example y define una contraseña.
- **Puerto ocupado:** identifica quién usa 3000 o 9090; no detengas servicios de otros proyectos.
- **Node Exporter DOWN:** revisa los logs del servicio y el acceso del daemon a los montajes.
- **Panel sin datos:** espera dos scrapes, revisa el rango temporal y Prometheus Targets.
- **Dashboard no aparece:** revisa el JSON montado y logs de Grafana. Provisioning reescanea cada 30 s.
- **Password olvidado:** revisa el valor local de .env; no borres volúmenes como primera medida.

Más pasos en [docs/troubleshooting.md](docs/troubleshooting.md).

## Próximas fases

1. Evaluar autenticación/ACL por contenedor cliente si el entorno deja de ser un laboratorio local; Docker Desktop proxya el origen a loopback.
2. Targets separados para Windows y/o Ubuntu WSL.
3. Automatización de despliegue y recuperación.

Alertmanager e historial local de incidentes forman parte de la fase 2. cAdvisor no se desplegó porque exige sockets sensibles; la alternativa host-side de Fase 3B y sus límites están documentados en [docs/container-monitoring-assessment.md](docs/container-monitoring-assessment.md) y [docs/container-exporter.md](docs/container-exporter.md).

## Evidencias para GitHub

No hay capturas ficticias. Cuando el laboratorio funcione, captura el dashboard con datos reales, Prometheus Targets con los dos jobs UP, docker compose ps y evidencia de node_uname_info. La lista está en [screenshots/README.md](screenshots/README.md). Oculta credenciales, datos personales y rutas locales.
