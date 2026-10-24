# Exporter local de métricas Docker

## Arquitectura

`services/container-exporter/container_exporter.py` se ejecuta como proceso foreground de Python en Windows; no es un contenedor ni modifica Docker Compose. Invoca solamente `docker ps` y `docker stats --no-stream` con el contexto `desktop-linux`, aplica el filtro exacto `com.docker.compose.project=it-infrastructure-monitoring-center` y consulta únicamente los IDs y estadísticas de esos contenedores. No inspecciona configuración, variables de entorno, mounts ni logs del motor.

Se evaluó un cliente Python del Docker Engine API: el Docker SDK no estaba instalado. En Windows eso requeriría añadir un cliente y su transporte para el named pipe, pero usaría el mismo acceso privilegiado del usuario y no reduciría el riesgo del daemon. La CLI ya instalada entregó en JSON/template los cinco snapshots reales, permite limitar campos de salida y reutiliza el contexto `desktop-linux`; por esas razones se eligió la CLI con timeout y análisis controlado en vez de instalar dependencias.

Prometheus consulta `host.docker.internal:9464` mediante el job `container-exporter`. La escucha del exporter está fijada en `127.0.0.1`; el servicio no escucha en la IP Wi-Fi, interfaces Ethernet ni `0.0.0.0`. Docker Desktop resuelve ese nombre a una dirección interna de su red privada, que puede variar por instalación. Se comprobó desde el contenedor Prometheus que una solicitud HTTP llegó a un listener temporal de loopback y que Windows vio el peer como `127.0.0.1`. No se creó ni modificó ninguna regla de firewall.

La dirección loopback evita publicar un listener directo a LAN. Docker Desktop hace de proxy para la ruta desde contenedores al host; esa ruta no identifica al contenedor cliente a nivel del exporter. Por tanto, otros contenedores del Docker Desktop podrían consultar este endpoint de solo lectura. El contenido está limitado a estadísticas y etiquetas de servicio/ID corto del proyecto seleccionado; no incluye nombres completos, imágenes, rutas ni variables de entorno. Si se requiere una ACL exclusiva por contenedor cliente, esta ruta no la proporciona y debe elegirse otra arquitectura.

## Privilegios y límites de seguridad

El proceso hereda el acceso Docker del usuario actual a través de la CLI y el contexto Windows `desktop-linux`. **Aunque el exporter solo ejecuta consultas de lectura, el proceso conserva privilegios significativos sobre el motor Docker**: una vulnerabilidad o modificación maliciosa del código podría usar la misma CLI para administrar contenedores. No se crea una credencial adicional, no se monta el socket en ningún contenedor y no se elevan capacidades. Ejecuta el exporter solo desde un checkout confiable y detenlo con `Ctrl+C` cuando no lo necesites.

Controles implementados:

- El proyecto se filtra por la etiqueta Compose; las respuestas de otros proyectos se descartan. Esa etiqueta es un filtro de alcance, no una frontera de autorización: un usuario con acceso al daemon puede cambiarla.
- `docker ps` devuelve solo ID corto y etiqueta de servicio. `docker stats` usa un formato acotado con ID, CPU, memoria y red; no procesa nombres completos.
- Subprocess sin shell, timeout por comando de 8 s, una consulta periódica de 15 s y salida máxima acotada.
- Errores de CLI se reducen a categorías fijas; no se registran stderr, respuestas completas, credenciales ni datos del motor.
- Solo se publican etiquetas `service` y `container_id` de 12 caracteres hexadecimales. Límite estricto de 32 contenedores: al excederse, la colección falla en vez de truncar una selección arbitraria.
- HTTP solo implementa GET para `/health` y `/metrics`; POST, PUT, PATCH y DELETE devuelven 405. No existe endpoint para ejecutar comandos y `/metrics` no usa autenticación.
- El proceso maneja Ctrl+C/SIGTERM y no realiza acciones administrativas de contenedores.

El endpoint `/health` es una comprobación de vida del servidor HTTP. La métrica `itmc_container_exporter_collection_success` refleja por separado si la consulta a Docker funcionó; el dashboard también presenta la antigüedad de la última colección completa.

## Métricas y semántica

| Métrica | Tipo | Interpretación |
|---|---|---|
| `itmc_container_exporter_observed_containers` | gauge | Filas del último snapshot utilizable de `docker stats`, no inventario completo del daemon |
| `itmc_container_cpu_usage_percent` | gauge | Porcentaje CPU del snapshot que calcula Docker CLI; puede superar 100 % por contenedor al usar varios núcleos |
| `itmc_container_memory_usage_bytes` | gauge | Uso mostrado por Docker CLI. En Linux, Docker CLI puede restar caché a diferencia del endpoint bruto de API |
| `itmc_container_memory_limit_bytes` | gauge | Límite que muestra Docker CLI, a menudo la memoria asignada al motor en Docker Desktop |
| `itmc_container_network_receive_bytes_total` / `...transmit_bytes_total` | counter | RX/TX acumulados reportados por Docker CLI; Grafana usa `rate()` para mostrar bytes/s |
| `itmc_container_last_observed_timestamp_seconds` | gauge | Hora Unix del snapshot para ese contenedor |
| `itmc_container_exporter_collection_success` | gauge | 1 para la última colección completa; 0 en error/parcial |
| `itmc_container_exporter_last_success_timestamp_seconds` | gauge | Hora de la última colección completa, para calcular frescura |

Solo se incluyen contenedores en ejecución que el filtro del proyecto Compose devuelve. Los IDs cortos distinguen instancias escaladas del mismo servicio sin divulgar los nombres completos. Docker CLI reporta `NetIO` como valores acumulados formateados con unidades humanas; el exporter los convierte a bytes y soporta prefijos decimales/binarios. Una recreación del contenedor puede reiniciar esos contadores, que Prometheus `rate()` trata como reset.

## Uso en PowerShell

Requisitos: Docker Desktop en ejecución, contexto `desktop-linux`, Docker CLI en `PATH` y Python 3.10+ (`py -3`). No se instala ningún paquete Python.

En una ventana PowerShell desde la raíz del repositorio:

~~~powershell
.\scripts\run-container-exporter.ps1
~~~

El script comprueba el contexto y ejecuta Python en primer plano enlazado a loopback. Para detenerlo, pulsa `Ctrl+C`. En otra ventana valida el proceso, Prometheus y Grafana:

~~~powershell
.\scripts\validate-container-exporter.ps1
~~~

### Inicio después de reiniciar Windows

El proyecto **no instala ni configura** un servicio de Windows, una tarea programada o un acceso directo de Inicio. La inspección local tampoco encontró una tarea o acceso directo que invoque el exporter. El proceso foreground termina al cerrar su ventana o reiniciar Windows; Prometheus dejará de mostrar métricas recientes hasta volver a iniciarlo.

Después de iniciar sesión, abre Docker Desktop y espera a que el motor Linux esté listo. Desde PowerShell, en la raíz del repositorio, confirma `docker context show` como `desktop-linux` y ejecuta `docker info` para comprobar que el Engine responde; luego inicia el exporter con:

~~~powershell
.\scripts\run-container-exporter.ps1
~~~

Déjalo ejecutándose en esa ventana. Para detenerlo, pulsa `Ctrl+C`; para reiniciarlo, detenlo así y vuelve a ejecutar el mismo comando. No finalices un PID que use el puerto 9464 sin identificar primero el proceso propietario.

Como alternativa futura, se puede configurar una tarea de Task Scheduler **al iniciar sesión del usuario**, con un breve retraso y una acción que espere de forma acotada a que `desktop-linux` y `docker info` estén disponibles antes de lanzar el mismo script. Debe ejecutarse con el mismo usuario no elevado que ya tiene acceso a Docker, reiniciarse ante salida inesperada y permitir detenerse desde Task Scheduler. Esa tarea no existe todavía y no se instaló; requeriría autorización antes de configurarla porque ejecuta código con acceso significativo al daemon.

El job `container-exporter` usa un scrape de 15 s. Para cargar el cambio de Prometheus sin detener ni recrear el contenedor:

~~~powershell
docker compose kill -s HUP prometheus
~~~

Grafana aprovisiona el JSON independiente desde `config/grafana/dashboards/` con el proveedor de archivos existente (escaneo periódico de 30 s). El dashboard de infraestructura de 14 paneles no se reemplaza ni se modifica.

## Dashboard

`Docker Container Monitoring` muestra seis KPIs superiores (contenedores observados, CPU y memoria agregadas, RX, TX y estado del exporter), tendencias CPU/memoria/red por servicio e ID corto, la edad de observación por contenedor y estado/frescura de la colección. Los agregados se basan solo en las series observadas para este proyecto. Si el proceso local no está activo, el target cae; `/health` y la métrica de estado distinguen proceso HTTP activo de motor Docker accesible.

Los indicadores de estado (`Compose Containers Observed`, `Exporter Health`, `Docker Stats Collection` y `Last Complete Sample Age`) usan consultas instantáneas y un único valor agregado. El panel Grafana conserva `lastNotNull` como reducción y no muestra todos los valores/series del vector. La ausencia de una muestra se representa como `NO DATA`; no se genera un `0` artificial ni se convierte ausencia en `UP`. `Exporter Health` usa `up` de Prometheus (alcance del endpoint), mientras `Docker Stats Collection` usa `itmc_container_exporter_collection_success` (resultado de la consulta real a Docker). `Last Complete Sample Age` confirma la frescura de la última colección completa.

Las consultas agregadas filtran por `job="container-exporter"` y requieren tanto `up == 1` como `collection_success == 1` cuando muestran estadísticas/conteos. Esto evita presentar un snapshot antiguo como una colección actual exitosa; los paneles de estado mantienen por separado la evidencia de fallo o ausencia.

## Limitaciones Docker Desktop / WSL2

- CPU, memoria y tráfico son estadísticas del motor Linux de Docker Desktop; no métricas del Windows físico ni de cada interfaz Windows.
- La métrica depende de que Docker Desktop y el contexto `desktop-linux` estén disponibles. Si el Engine está detenido, el timeout mantiene vivos HTTP y Prometheus, pero la colección indica error.
- `docker stats --no-stream` entrega un snapshot formateado, no las estructuras crudas del Engine API. La unidad humana se analiza en el host y puede requerir mantenimiento si Docker cambia su formato CLI.
- La memoria sigue la semántica de presentación de Docker CLI (incluida la consideración de cache en Linux), no una lectura idéntica de cgroup/Engine API.
- La ruta host de Docker Desktop proxyada a loopback no permite diferenciar qué contenedor originó la conexión. El exporter no se expone a LAN, pero no debe tratarse como API privada solo para Prometheus.
- No es un reemplazo de cAdvisor para métricas de filesystem, dispositivos, procesos ni series detalladas por interfaz.

## Referencias

- [Docker Desktop: conexión de un contenedor a un servicio del host](https://docs.docker.com/desktop/features/networking/networking-how-tos/)
- [Docker Desktop: red y comportamiento del backend/firewall](https://docs.docker.com/desktop/features/networking/)
- [Docker CLI `container stats`, unidades y limitaciones de memoria](https://docs.docker.com/reference/cli/docker/container/stats/)
