# Auditoría final de fases 1 y 2

**Proyecto:** IT Infrastructure Monitoring Center
**Fecha:** 2026-10-24
**Alcance:** estado operativo, Prometheus/Alertmanager, reglas, dashboard provisionado, pruebas y preparación para el primer commit local.
## Dictamen

**APROBADA CON OBSERVACIONES.** La plataforma, las reglas, las consultas del dashboard y ambos validadores pasan en el momento de la auditoría. La causa histórica del código 255 de Prometheus no puede confirmarse con los artefactos que siguen disponibles, y una de las dos notificaciones RESOLVED tardó 90.009 s. No hay evidencia de que el segundo retraso proceda de una falla del webhook del logger.

**Primer commit local:** sí, el conjunto versionable auditado es seguro para un commit local usando el flujo normal de Git, siempre que se revise el listado preparado antes de confirmar y no se fuerce la inclusión de `.env` o `data/`. No ejecuté `git add`, `git commit` ni `git push`. No publiqué nada.

Durante esta auditoría no cambié Docker Compose, Prometheus, Alertmanager, Grafana, Node Exporter, redes ni reglas. El único archivo creado es este informe.

## 1. Cierre de Prometheus con código 255

El código 255 y la recuperación con `docker compose up -d prometheus` forman parte del estado comunicado por el operador. El contenedor anterior ya no conserva ese estado en `docker inspect`; por tanto, el código de salida histórico no se puede verificar allí.

Evidencia disponible después de la recuperación:

- El contenedor actual está **running/healthy**, con `ExitCode=0`, `RestartCount=0` y sin error actual. Fue creado el 2026-10-23 a las 22:17 hora local y su inicio actual es el 2026-10-24 a las 08:03:54 hora local; es el contenedor existente, no una recreación durante esta auditoría.
- Los logs muestran un arranque anterior a las 03:17Z y el arranque actual a las 13:03Z. En el actual, Prometheus reemplazó un lockfile previo, reprodujo correctamente el WAL y quedó listo para servir. No encontré mensajes de corrupción del WAL, panic ni fatal asociados a ese arranque.
- El flujo de eventos de Docker disponible no contiene un evento `die`/`oom` que conserve el código 255 anterior. Sí contiene el `start` actual y eventos posteriores de healthcheck. El estado de `inspect` es posterior a la recuperación y no reconstruye el estado anterior.
- Los otros cuatro contenedores comenzaron alrededor de las 07:58 hora local; Prometheus comenzó alrededor de las 08:03. Los logs de Docker Desktop/backend vuelven a registrar actividad de WSL cerca de las 07:58 y Windows registra eventos informativos de Hyper-V Virtual Switch en ese intervalo. Las distribuciones `Ubuntu` y `docker-desktop` aparecen actualmente en estado Running.

**Confirmado:** Prometheus tuvo una segunda inicialización y se recuperó; el WAL pudo reproducirse; existía un lockfile de una ejecución previa. El conjunto de arranques de servicios y la actividad del backend de Docker Desktop/WSL coinciden en el tiempo.

**Hipótesis, no causa demostrada:** una interrupción o reinicialización del backend Docker Desktop/WSL pudo terminar Prometheus de forma no limpia y explicar el lockfile. No hay un evento histórico que enlace esa secuencia directamente con el código 255, ni un log que identifique el proceso o señal que terminó Prometheus. El lockfile es indicio de cierre no limpio, no prueba de la causa.

## 2. Reportes de incidentes y latencia RESOLVED

Los dos reportes finales son exitosos y dejan `TestIncident` restaurada. Todos los tiempos siguientes están en UTC.

| Reporte | Resultado | Prometheus RESOLVED | Logger recibió RESOLVED | Diferencia | Recuperación total |
|---|---:|---:|---:|---:|---:|
| `incident-test-20261024T031816Z.json` | passed | 03:18:13.011Z | 03:18:13.016Z | 0.005 s | 13.836 s |
| `incident-test-20261024T032046Z.json` | passed | 03:19:13.011Z | 03:20:43.020Z | 90.009 s | 104.168 s |

En ambos, el logger recibió FIRING unos 15 s después de la observación de Prometheus (15.005 s y 15.003 s). Para el segundo RESOLVED, el log de acceso del logger no registra un `POST /webhook` antes de las 03:20:43Z y la observación de la API de Alertmanager lo vio inactivo en ese mismo momento. Esto sitúa los 90 s antes de la recepción por el logger, no dentro del procesamiento del logger.

La configuración vigente y cargada de Alertmanager es `group_wait=3s`, `group_interval=5s`, `repeat_interval=4h`, `resolve_timeout=5m` y `send_resolved=true`. Por sí solos, `group_wait` y `group_interval` no explican una espera de 90 s. En la serie histórica de Prometheus para la ventana de la prueba, `prometheus_notifications_errors_total` permaneció en 0 y `prometheus_notifications_queue_length` en 0; los logs de Alertmanager consultables no muestran un error correlacionado. No hay evidencia suficiente para atribuir el retraso a una causa exacta. Es compatible con demora de despacho/recepción entre Prometheus y Alertmanager, pero eso sigue siendo una hipótesis.

Recomendación: si vuelve a ocurrir, conservar inmediatamente logs de ambos componentes y las muestras de `prometheus_notifications_*` y `alertmanager_*` antes de reiniciar servicios. No cambié intervalos ni configuración porque la evidencia actual no justifica un ajuste concreto.

Hay siete JSON de simulación en `data/`: cinco marcados `passed` y dos `failed`. Los dos reportes más recientes son los dos `passed` analizados arriba; los anteriores incluyen intentos previos y no sustituyen esos resultados finales. `data/` está excluido de Git.

## 3. Intervalos y reglas

- Prometheus: `scrape_interval=15s` y `evaluation_interval=15s`; grupo de reglas de infraestructura también cada 15 s.
- Alertmanager: `group_wait=3s`, `group_interval=5s`, `repeat_interval=4h`, `resolve_timeout=5m`, con envíos de resolución activados.
- Las cuatro reglas de producción están cargadas y saludables: `TargetDown` (1 min), `HighCPUUsage` (>85% por 5 min), `HighMemoryUsage` (>90% por 5 min) y `DiskSpaceCritical` (>90% de `/mnt/host/c` por 5 min).
- La quinta regla, `TestIncident`, usa `vector(0) == 1`; la API de reglas la muestra `inactive/ok` y la API de Alertmanager no tiene ninguna instancia activa de esa alerta.
- El validador de reglas Prometheus pasa tanto `promtool check rules` como `promtool test rules`; `amtool check-config` también pasa.

Los porcentajes de CPU y RAM describen el Linux visible al motor Docker/WSL2, no el Windows físico. La identidad consultada fue Linux con kernel `microsoft-standard-WSL2`. Los paneles llevan esa indicación; el disco C: se etiqueta por `/mnt/host/c` y el volumen virtual Docker por `/mnt/docker-desktop-disk`.

## 4. Servicios, targets y datos

En la última comprobación, los cinco servicios estaban saludables:

| Servicio | Estado | Publicación de puerto |
|---|---|---|
| Prometheus | healthy | `127.0.0.1:9090` |
| Grafana | healthy | `127.0.0.1:3000` |
| Alertmanager | healthy | `127.0.0.1:9093` |
| Node Exporter | healthy | Sin puerto publicado al host |
| Incident Logger | healthy | Sin puerto publicado al host |

Los dos targets activos de Prometheus, `prometheus` y `node-exporter`, respondieron `UP`. Las consultas comprobaron muestras reales de CPU, memoria, filesystem, red, uptime e identidad de Node Exporter. La API de salud de Grafana respondió, el datasource provisionado apunta al Prometheus interno y su comprobación de salud fue `OK`.

## 5. Dashboard y aprovisionamiento

Grafana devolvió el dashboard **IT Infrastructure Overview** marcado como provisionado desde `infrastructure-overview.json`. Se conserva el aprovisionamiento por archivos: `dashboard-provider.yml` apunta a `/var/lib/grafana/dashboards`, el archivo del dashboard se monta en esa ruta y el proveedor actualiza cada 30 s. No depende de una importación manual.

La API cargó **14 paneles**: seis KPIs en la fila superior, cuatro tendencias, tres paneles de estado y capacidad, y una vista de capacidad de filesystem. La geometría validada para la fila de tendencias a 1920×1080 es cuatro paneles de 6 columnas por 7 unidades de altura. Los paneles muestran datos al consultar Prometheus.

Consultas principales y unidades:

- CPU: `100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{job="node-exporter",mode="idle"}[$__rate_interval])))`, en porcentaje.
- Memoria: `100 * (1 - (node_memory_MemAvailable_bytes{job="node-exporter"} / node_memory_MemTotal_bytes{job="node-exporter"}))`, en porcentaje.
- C: `100 * (1 - node_filesystem_avail_bytes{job="node-exporter",mountpoint="/mnt/host/c"} / node_filesystem_size_bytes{job="node-exporter",mountpoint="/mnt/host/c"})`, en porcentaje.
- Red RX/TX: `sum(rate(node_network_receive_bytes_total{job="node-exporter",device!~"lo|veth.*|docker.*|br-.*"}[$__rate_interval]))` y `sum(rate(node_network_transmit_bytes_total{job="node-exporter",device!~"lo|veth.*|docker.*|br-.*"}[$__rate_interval]))`, en bytes/s.
- Uptime: `time() - node_boot_time_seconds`, con unidad de duración.
- Targets: `sum(up{job=~"prometheus|node-exporter"})` y `count(up{job=~"prometheus|node-exporter"})`; el valor consultado fue **2/2**. Se eliminó la redundancia de panel, aunque Grafana representa UP y Total como dos lecturas dentro del mismo Stat, no como una cadena única `2/2`. `Target Health` conserva el estado individual por target, con mapeo 0→DOWN y 1→UP.

Filesystem Capacity restringe `mountpoint` a `/mnt/host/c` y `/mnt/docker-desktop-disk`; excluye dispositivos `/dev/loop*` y `tmpfs`, `overlay`, `squashfs`, `proc`, `sysfs`, `cgroup*`, `nsfs` y `devtmpfs`. `max by (mountpoint)` reduce las dimensiones de serie `device`, `fstype` e `instance` a una serie por punto de montaje lógico. No usa `topk`. En la consulta real actual se observó una serie por volumen: Windows C: **23.11%** y Docker virtual disk **5.73%**. La serie actual de Docker tiene `device=/dev/sde`, `fstype=ext4`, `mountpoint=/mnt/docker-desktop-disk` e `instance=node-exporter:9100`.

La multiplicación anterior de filas con el mismo punto de montaje corresponde al modelo de series de Prometheus: las diferencias en `device`, `fstype` o `instance` crean series distintas aunque `mountpoint` sea idéntico. La consulta actual agrupa por el volumen lógico. No pude reproducir la multiplicidad en los datos vivos actuales, que ya entregan una serie para el volumen Docker y otra para C:. Esta agregación es adecuada para el despliegue actual de un solo Node Exporter; si se agregan hosts, se debe conservar también `instance` para no combinar volúmenes de máquinas diferentes.

Umbrales visibles en los archivos: CPU 70/85% (advertencia/crítico), memoria 75/90%, disco C: 75/90% y filesystem 70/85%. La unidad de red es `binBps`, que Grafana presenta como tasa de bytes legible. La unidad de uptime es duración en segundos, que Grafana humaniza.

No hice inspección visual ni captura: la pestaña disponible estaba en `/login` y no había una sesión autorizada abierta. No inicié sesión ni capturé credenciales; la comprobación del dashboard se hizo por API autenticada usando la configuración local sin imprimir sus valores.

## 6. Git, `.gitignore` y revisión de archivos

No hay commits previos ni archivos staged. Antes de crear este informe, `git ls-files --others --exclude-standard` encontró **27** candidatos, no 26. El inventario auditado fue:

```text
.env.example
.gitignore
LICENSE
README.md
compose.yaml
config/alertmanager/alertmanager.yml
config/grafana/dashboards/infrastructure-overview.json
config/grafana/provisioning/alerting/.gitkeep
config/grafana/provisioning/dashboards/dashboard-provider.yml
config/grafana/provisioning/datasources/prometheus.yml
config/grafana/provisioning/plugins/.gitkeep
config/prometheus/prometheus.yml
config/prometheus/rules/infrastructure.yml
config/prometheus/test-rules/incident-test.yml
config/prometheus/tests/infrastructure.test.yml
docs/architecture.md
docs/troubleshooting.md
docs/verification-results.md
screenshots/README.md
scripts/simulate-incident.ps1
scripts/simulate-incident.sh
scripts/simulate_incident.py
scripts/validate.ps1
scripts/validate.sh
services/incident-logger/Dockerfile
services/incident-logger/incident_logger.py
services/incident-logger/test_incident_logger.py
```

El informe añade un archivo más; el inventario actual previo al staging es de 28 candidatos. `.gitignore` excluye `.env`, `.env.*` salvo `.env.example`, `data/`, bases/WAL/logs/salidas, bytecode y cachés de Python, y archivos comunes de editor/OS. Se comprobó que `.env`, reportes de `data/` y `__pycache__` están ignorados. El valor de contraseña de `.env.example` es un marcador de posición; los validadores leen las credenciales locales de `.env` para la API, pero no imprimen su valor.

El escaneo de los candidatos encontró únicamente el marcador de posición y referencias de código a variables de entorno; no encontró claves privadas, tokens Bearer, claves AWS ni contraseñas literales. No hay capturas de imagen candidatas en `screenshots/`, solo su README. No imprimí ni capturé el contenido de `.env`; los validadores lo leyeron internamente para autenticarse contra Grafana sin mostrar sus valores. La cantidad distinta de la esperada (27 en vez de 26) es una diferencia de inventario, no un archivo secreto detectado.

Controles de alcance observados en Compose: puertos de las UIs publicados solo en loopback; Node Exporter y el logger no publican puertos al host; el logger corre como usuario no root; los contenedores aplican filesystem de solo lectura, `no-new-privileges` y eliminación de capabilities. Grafana tiene acceso anónimo y altas deshabilitadas. La cookie `Secure` está desactivada porque el endpoint local usa HTTP; mantenerlo en loopback es apropiado para este entorno local, pero no se debe exponer a una red o Internet sin TLS y controles de acceso apropiados.

## 7. Validación ejecutada

Pasaron ambos validadores sin reiniciar ni detener servicios:

- `scripts/validate.ps1` — código de salida 0.
- `wsl -d Ubuntu -- bash ./scripts/validate.sh` — código de salida 0.
- En ambas ejecuciones: configuración y reglas Prometheus válidas, pruebas `promtool` exitosas, configuración de Alertmanager válida, 4 pruebas unitarias del logger exitosas, cinco servicios saludables, dos targets UP, datasource Prometheus saludable, ausencia de `TestIncident` activa y dashboard provisionado con 14 paneles.
- Los validadores comprobaron consultas de dashboard con resultados, una serie por volumen lógico y distribución equilibrada de las tendencias.

El resultado APROBADA CON OBSERVACIONES se debe a la causa no demostrada del código 255, la demora RESOLVED de 90.009 s sin diagnóstico definitivo y la ausencia de inspección visual autenticada. Ninguna de esas observaciones impide un primer commit **local** seguro del inventario versionable; el retraso RESOLVED debe quedar como seguimiento operativo antes de dar por cerrado el comportamiento de notificaciones.
