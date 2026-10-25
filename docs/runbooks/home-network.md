# Runbook — Home Network Monitoring

## Reglas de operación

- No escanear una red ni agregar destinos no aprobados.
- No publicar el puerto `9115`, no crear port forwarding/UPnP y no agregar Blackbox a otras redes sin revisión.
- No añadir `privileged` ni capabilities por conveniencia. Si ICMP no funciona con ping sockets restringidos, diferenciar la sonda bloqueada de un router caído.
- No copiar la dirección privada local a documentación, issues, capturas o Git.
- Windows y Docker Desktop/WSL2 deben estar despiertos y conectados para producir datos continuos.

## Preparación local

Ejecutar en PowerShell, desde la raíz del repositorio. Esto crea un archivo ignorado por Git; editar la copia local y reemplazar su placeholder por la única IP privada autorizada del router. No mostrar ni pegar el contenido en registros compartidos.

~~~powershell
if (-not (Test-Path config/prometheus/targets/home-network.yml)) {
  Copy-Item config/prometheus/targets/home-network.example.yml config/prometheus/targets/home-network.yml
}
notepad config/prometheus/targets/home-network.yml
git check-ignore config/prometheus/targets/home-network.yml
python scripts/ci/validate_blackbox.py
~~~

El archivo debe contener un grupo con una dirección IP literal privada y únicamente la etiqueta genérica `probe_name: home-router`. El validador nunca imprime la dirección.

## Activación controlada — solo para un entorno aún no activado

La validación de esta rama encontró el servicio activo. Durante la auditoría no se
ejecutaron comandos de administración ni se alteró el runtime. Para una instalación
que todavía no esté activa, solicita una ventana autorizada y comprueba que Docker
Desktop está iniciado y el contexto corresponde a Linux:

~~~powershell
docker context show
docker info
docker compose config --quiet
python scripts/ci/validate_blackbox.py
~~~

La activación requiere crear la red dedicada y el nuevo servicio, y reconciliar Prometheus con su configuración actualizada. Puede recrear Prometheus y causar una breve interrupción de scraping. Ejecutarla solo en una ventana aprobada:

~~~powershell
docker compose up -d blackbox-exporter prometheus
docker compose ps
~~~

Estas instrucciones no incluyen crear/tocar servicios ajenos ni ejecutar un barrido. Para comprobar luego, usar Prometheus en loopback, la API local o el dashboard. No declarar sano por la mera presencia de `up` del exporter.

## Comprobación de datos reales

1. En Prometheus `/targets`, comprobar `blackbox-exporter` y los jobs domésticos. `UP` en el exporter solo confirma el scrape de su proceso.
2. Consultar `up{job="blackbox-exporter"}`; debe existir una muestra reciente con valor `1`.
3. Consultar `up{job="home-router-icmp"}` y `probe_success{job="home-router-icmp"}`. La primera debe estar en `1` y la segunda en `1` para afirmar que la sonda de router respondió.
4. Consultar `probe_success{job=~"home-internet-icmp|home-dns-a|home-dns-aaaa|home-https"}` y verificar timestamps actuales de cada destino.
5. Abrir en Grafana el dashboard `Home Network Monitoring`; un panel vacío significa que falta configuración, no que el servicio esté UP.
6. Comprobar la API/datasource de Prometheus desde Grafana solo cuando los servicios estén encendidos.

Ejemplos de consulta, sin valores privados:

~~~promql
up{job="blackbox-exporter"}
up{job=~"home-.*"}
probe_success{job=~"home-.*"}
probe_duration_seconds{job=~"home-.*"}
~~~

## Diagnóstico por capa

Evalúa las señales en este orden y conserva la diferencia entre un fallo del sistema
de monitorización y un fallo del destino:

1. `up{job="blackbox-exporter"}` indica si Prometheus puede scrapear el proceso
   Blackbox. Si vale `0` o no hay muestra, la salud de los destinos es **desconocida**;
   no concluyas que el router o el ISP están caídos.
2. `up{job=~"home-.*"}` indica si Prometheus obtuvo una respuesta del endpoint `/probe`.
   `0` es un error de scrape; ausencia de series requiere revisar discovery/configuración.
3. `probe_success{job=...}` indica el resultado de la prueba del destino solo cuando
   hay una muestra. `0` es fallo de esa prueba; ausencia no equivale a éxito ni a cero.
4. Compara las sondas: router sano más DNS/HTTPS externos fallidos orienta a un
   problema de salida, resolución o servicio externo, pero no demuestra por sí solo
   una caída del ISP. Corrobora con una prueba independiente y con el mismo intervalo.

| Síntoma | Interpretación inicial | Pasos seguros |
|---|---|---|
| No hay series de exporter | Servicio sin iniciar, target no descubierto o config no cargada. | Confirmar Docker Desktop, `docker compose ps`, logs del exporter con el comando acotado de abajo y estado del target Prometheus. |
| Exporter `up=0` o sin muestra | Prometheus no puede confirmar la salud del exporter; los destinos quedan desconocidos. | Revisar salud/logs del servicio, red dedicada y errores de configuración. No clasificarlo como caída del ISP. |
| `up` de un job de sonda es `0` | El scrape de `/probe` dio error; no es un `probe_success=0` normal. | Revisar el error scrape en Prometheus y logs del exporter; separar timeout/HTTP del resultado de la sonda. |
| Exporter `up=1`, router `probe_success=0` | ICMP al router falla, está filtrado o la ruta del contenedor no alcanza el gateway. | Comparar con un chequeo puntual previamente autorizado desde Windows y comprobar la ruta del contenedor; no cambiar permisos primero. |
| Router OK; ICMP público falla | Posible bloqueo de ICMP exterior o problema de salida. | Comparar DNS/HTTPS estáticos permitidos. No inferir caída total de Internet de una sola sonda ICMP. |
| Router OK; DNS A/AAAA falla | Consulta al resolver externo aprobado falla. | Revisar `probe_dns_lookup_time_seconds`, `probe_dns_duration_seconds` y errores del probe; confirmar que la red del contenedor permite DNS hacia el destino aprobado. |
| Router OK; HTTPS falla | Posible resolución Docker-side, TLS, salida o indisponibilidad del sitio externo. | Revisar fases `probe_http_duration_seconds`, status code y logs; corroborar con otra evidencia antes de incidente externo. La sonda no sigue redirects a otro host. |
| Sin router target | El archivo local no existe, está mal formado o no se montó. | Revisar configuración local sin imprimir el archivo, permisos/ruta bind y alerta `HomeRouterTargetMissing`. |

Para logs, evita volcar dumps de configuración o entorno. Ejecuta solo si el usuario inicia el entorno:

~~~powershell
docker compose logs --tail 100 blackbox-exporter
docker compose logs --tail 100 prometheus
~~~

## Alertas e incidentes

Las reglas esperan 1m para exporter/errores de ejecución, 2m para router/DNS/target ausente y 3m para HTTPS. Las reglas dependientes de Internet exigen además router `probe_success=1`. Las pruebas `promtool test rules` simulan FIRING, supresión y recuperación; no desconectan la red.

Alertmanager conserva su configuración actual y entrega también RESOLVED al Incident Logger. Revisa los registros locales del logger según el procedimiento existente; no adjuntes incidentes reales a repositorios públicos sin anonimizar.

Un evento ocurrido durante preparación o cambios de configuración debe clasificarse
como **PREPARACIÓN** aunque produzca una alerta real del exporter. Eso confirma un
problema de observabilidad/disponibilidad del exporter, pero no una interrupción del
ISP. Usa **INCIDENTE_REAL_DE_DESTINO** solo si `probe_success` falla con muestras
presentes y evidencia independiente lo corrobora. Marca como **SIMULACIÓN** cualquier
estado generado por `promtool`, pruebas o ejercicios. La ausencia de datos durante un
intervalo deja el estado del destino indeterminado; no permite afirmar que estuvo sano
ni que estuvo caído.

El incidente de preparación redactado y su línea de tiempo están en
[`docs/incidents/blackbox-exporter-preparation.md`](../incidents/blackbox-exporter-preparation.md).

## Reversión

En una ventana aprobada:

1. Revisa y revierte los jobs de sonda y reglas domésticas en los archivos Prometheus; no cambies las reglas existentes.
2. Revisa la configuración resultante y aplica el cambio solo a Prometheus. Esta operación puede recrear Prometheus y producir una pausa breve de scraping:

~~~powershell
docker compose config --quiet
docker compose up -d prometheus
~~~

3. Cuando Prometheus ya no consulte Blackbox, detén únicamente el exporter nuevo:

~~~powershell
docker compose stop blackbox-exporter
~~~

El exporter no tiene volumen de datos. No usar `docker compose down --volumes` ni borrar volúmenes. Si también se quiere retirar el dashboard o la definición del servicio/red, hacerlo como una revisión de archivos separada y aplicar después de inspeccionar el diff.

En el checkout de trabajo, revertir archivos versionados solo después de revisar `git diff`; eliminar el target local es optativo y se hace manualmente. No se automatiza una limpieza destructiva.
