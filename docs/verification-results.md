# Resultados de validación

Fecha: 2026-10-23 (hora de Panamá). Las pruebas de ejecución se realizaron con Docker Desktop activo.

## Entorno observado

- Windows 11 Home x64; memoria visible: 31.22 GB.
- WSL2 con Ubuntu y docker-desktop; Docker Desktop usa el contexto desktop-linux.
- Docker CLI/Engine 29.9.2, Docker Desktop, x86_64. Docker Compose v5.6.1.
- El primer chequeo encontró Docker Desktop detenido; se inició para ejecutar la fase de validación.
- La métrica node_uname_info informó Linux, kernel 6.18.54.1-microsoft-standard-WSL2 y máquina x86_64. El nombre de host observado es el ID del contenedor Node Exporter porque no se comparte el namespace UTS.
- El filesystem /mnt/host/c apareció como 9p. Su espacio disponible coincidió con el espacio libre del volumen C: medido con Get-Volume durante la prueba.
- Conclusión: CPU, memoria, red y uptime son métricas del entorno Linux WSL2 del Docker Engine de Docker Desktop. Node Exporter no observa CPU/memoria de Windows ni Ubuntu como distro separada. El montaje 9p sí expuso el espacio disponible de C:.

## Comprobaciones realizadas y aprobadas

- docker compose config --quiet.
- promtool check config con Prometheus 3.15.0: configuración válida.
- Tags Docker Hub existentes para amd64: Prometheus v3.15.0, Grafana 13.2.4 y Node Exporter v1.12.1.
- Node Exporter y Prometheus contienen wget, usado por sus healthchecks.
- docker compose up -d completó sin errores en la configuración final.
- docker compose ps: los tres servicios healthy. Prometheus se publica en 127.0.0.1:9090, Grafana en 127.0.0.1:3000, Node Exporter queda solo en la red interna.
- Endpoints de salud de Prometheus y Grafana responden.
- Prometheus muestra ambos jobs UP y devuelve datos para CPU, memoria, filesystem, red, uptime e identidad Linux.
- Tras la revisión del dashboard v2, las 16 expresiones PromQL de los 14 paneles devolvieron series reales al evaluarse en Prometheus. La API de Grafana confirma la carga mediante el proveedor de archivos.
- La repetición de `/mnt/docker-desktop-disk` no venía de varias etiquetas activas o históricas: Prometheus devolvió un solo conjunto de etiquetas en seis horas (`job=node-exporter`, `instance=node-exporter:9100`, `device=/dev/sdd`, `fstype=ext4`, `mountpoint=/mnt/docker-desktop-disk`). El bar gauge usaba una consulta de rango con `reduceOptions.values=true` (mostrar todos los valores), generando una barra por muestra. Se cambió a consulta instantánea, cálculo de último valor y `max by (mountpoint)` para colapsar dimensiones `device`, `fstype`, `instance` y `job` por volumen lógico.
- La consulta agrupada devuelve una sola serie por punto de montaje: `/mnt/docker-desktop-disk` (5.70 % observado al validar, etiqueta Docker virtual disk) y `/mnt/host/c` (23.06 %, Windows C: compartido por 9p). También excluye dispositivos loop y sistemas de archivos virtuales.
- El KPI Targets UP / Total reúne los valores UP y Total en el mismo panel; Target Health conserva el estado individual por servicio y se eliminó Monitored Targets como panel separado.
- CPU y memoria se etiquetaron como métricas del Linux Engine (WSL2 en Docker Desktop), nunca del Windows físico. Las cuatro tendencias se distribuyeron en una fila para la vista 1920x1080.
- La API de Grafana confirma salud, datasource Prometheus accesible por DNS interno y dashboard provisionado automáticamente desde `config/grafana/dashboards/infrastructure-overview.json` mediante el proveedor existente.
- `scripts/validate.ps1` y `scripts/validate.sh` pasaron desde PowerShell y Ubuntu/WSL, incluyendo las consultas, una serie por mountpoint, el KPI combinado y los 14 paneles provisionados.
- La pestaña local del navegador estaba en `/login`; no había una sesión autorizada para inspección visual. No se automatizó el inicio de sesión ni se capturaron credenciales; la comprobación se hizo con la API de Grafana y consultas PromQL.
- Se ejecutó docker compose restart sin borrar volúmenes. La consulta histórica de up conservó muestras antes y después del reinicio para ambos jobs: ocho muestras previas y dos posteriores en la ventana consultada. Datasource, dashboard y healthchecks siguieron respondiendo.
- .env fue creado localmente con una contraseña aleatoria, está ignorado por Git y no se muestra en el repositorio. .env.example solo contiene un marcador.

## Problemas detectados y corregidos

1. Docker Desktop rechazó la propagación rslave al montar /. Se retiró esa opción; /proc, /sys y / siguen montados en solo lectura. Las métricas de filesystem confirmaron que los submontajes necesarios son visibles.
2. Docker Desktop no hizo accesibles los puertos publicados desde la red marcada internal. Se mantuvo itmc-monitoring como red interna para scraping y se añadió local-access solo a Grafana y Prometheus. Los puertos ahora se publican únicamente en loopback; Node Exporter no se conecta a local-access.
3. Grafana intentaba consultas externas de actualizaciones/reportes sin salida de red. Se desactivaron reporting y chequeos de actualizaciones; el datasource interno funciona.
4. Al montar el directorio de provisioning completo, Grafana reportó que faltaban las subcarpetas de alerting y plugins. Se añadieron ambas vacías; no se provisionan reglas ni plugins y los errores de ruta desaparecieron.

## Pendiente

No se creó captura visual de esta revisión. La pestaña de navegador seguía en `/login` sin sesión autorizada; se validó el dashboard por la API de Grafana y no se automatizó el acceso. No hay pruebas de recuperación ante corrupción de volúmenes ni endurecimiento de producción, que están fuera del alcance de esta fase.

## Actualización fase 2 — alertas e incidentes

Fecha de ejecución: 2026-10-24 UTC (2026-10-23, hora de Panamá). Se aplicó `docker compose up -d --build` sin bajar el stack ni eliminar volúmenes. Prometheus se recreó para cargar reglas y enviar alertas; Grafana y Node Exporter permanecieron healthy. El estado final muestra los cinco servicios healthy. Grafana y Prometheus conservan `127.0.0.1:3000` y `127.0.0.1:9090`; Alertmanager publica su UI solo en `127.0.0.1:9093`; `docker inspect` confirmó que incident-logger no tiene `HostConfig.PortBindings`.

### Validaciones

- `docker compose config --quiet`: válido.
- `promtool check config /etc/prometheus/prometheus.yml`: válido; cargó cuatro reglas productivas y una regla controlada.
- `promtool check rules` para ambos archivos de reglas: válido.
- `promtool test rules /etc/prometheus/tests/infrastructure.test.yml`: `SUCCESS`; incluye casos positivos/negativos y duraciones de 1 y 5 minutos.
- `amtool check-config /etc/alertmanager/alertmanager.yml`: válido, con una ruta y un receptor.
- La recarga de la regla de prueba usa SIGHUP al proceso mediante Docker Compose y espera `prometheus_config_last_reload_success_timestamp_seconds`; no se habilitó `--web.enable-lifecycle` ni `/-/reload`.
- Pruebas HTTP del logger: cuatro aprobadas, incluyendo FIRING idempotente ante reintento, RESOLVED diferenciado, validación de payload, ruta no válida y rotación limitada.
- API de Prometheus: ambos jobs (`prometheus`, `node-exporter`) UP; las cinco reglas cargadas y `TestIncident` inactiva después de la ejecución.
- API de Alertmanager y healthcheck del logger: responden. El logger no está publicado fuera de la red Docker.
- El validador de Grafana confirma salud del datasource Prometheus, sus consultas PromQL y los 14 paneles del dashboard provisionado `IT Infrastructure Overview`.

### Ciclo real FIRING → RESOLVED

Se probaron ambos wrappers con la configuración final y Prometheus recargando por SIGHUP. Los informes locales están bajo `data/` (ignorado por Git). Cada ensayo activó una regla dedicada, evitó generar carga real y restauró el archivo original byte por byte.

**PowerShell:** `data/incident-test-20261024T031816Z.json`.

- Activación solicitada: `2026-10-24T03:17:23.959Z`; primera muestra `ALERTS{alertstate="firing"}`: `03:17:43.011Z` (19.052 s).
- El logger recibió FIRING a `03:17:58.016Z`, 15.005 s después de esa muestra.
- La condición volvió a normal a `03:17:59.180Z`; Prometheus registró RESOLVED a `03:18:13.011Z` y el logger lo guardó a `03:18:13.016Z` (5 ms después).
- Tiempo desde restaurar la condición hasta el evento RESOLVED: 13.836 s.

**WSL/Bash:** `data/incident-test-20261024T032046Z.json`.

- Activación solicitada: `2026-10-24T03:18:26.686Z`; primera muestra FIRING: `03:18:43.011Z` (16.325 s).
- El logger recibió FIRING a `03:18:58.014Z`, 15.003 s después de esa muestra.
- La condición volvió a normal a `03:18:58.852Z`; Prometheus registró RESOLVED a `03:19:13.011Z`, y el logger recibió RESOLVED a `03:20:43.020Z` (90.009 s después).
- Tiempo total hasta el registro RESOLVED: 104.168 s desde restaurar la condición.

Las latencias anteriores son las realmente observadas. No se atribuye el retraso completo a `group_wait: 3s`: los ensayos incluyen evaluación de reglas, envío Prometheus, agrupación y notificación Alertmanager. El simulador separa las marcas de Prometheus, la observación de la API Alertmanager y las marcas de recepción del JSONL.

No se produjo una captura visual: la pestaña disponible no tenía sesión autorizada. La API verificó el dashboard y el datasource sin iniciar sesión por el navegador ni capturar credenciales.
