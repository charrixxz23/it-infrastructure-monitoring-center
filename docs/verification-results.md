# Resultados de validación

Este documento resume evidencia técnica de las fases 1–3B y CI, sin incluir capturas, exportaciones JSONL, reportes locales ni detalles identificativos de la máquina. Los resultados son una referencia de validaciones anteriores; los comandos reproducibles indicados abajo sirven para comprobar el estado actual del entorno.

## Runtime local comprobado

En las ejecuciones locales documentadas, Docker Desktop/WSL2 proporcionó el daemon Linux del proyecto:

- Los cinco servicios Compose alcanzaron estado healthy.
- Prometheus expuso tres targets UP: Prometheus, Node Exporter y el exporter de contenedores cuando este proceso host-side estaba iniciado.
- El datasource de Prometheus y los dashboards provisionados respondieron mediante sus APIs.
- IT Infrastructure Overview conservó 14 paneles y Docker Container Monitoring 12 paneles.
- Las expresiones PromQL de los dashboards devolvieron muestras reales durante las comprobaciones; los KPIs de estado no convierten ausencia de datos en salud.
- El exporter local devolvió estadísticas de los contenedores observados bajo la etiqueta Compose del proyecto. Las pruebas unitarias usaron respuestas Docker CLI simuladas.

CPU, memoria, red y uptime de Node Exporter describen el Linux visible al Docker Engine. No representan CPU ni memoria del Windows físico. Para métricas Docker por contenedor, el proceso Python en Windows debe iniciarse manualmente y Docker Desktop debe estar disponible.

## Pruebas y configuración

- Las pruebas unitarias históricas del Incident Logger y Container Exporter pasaron. El exporter se probó con Docker CLI simulado, incluidos errores, timeout, filtro de proyecto, formato de métricas y ausencia de metadatos no necesarios.
- promtool validó la configuración y las reglas de Prometheus; los rule tests pasaron.
- amtool validó la configuración de Alertmanager.
- La regla TestIncident quedó inactiva después de las simulaciones controladas FIRING → RESOLVED.
- La validación de Compose renderizó la configuración; el pipeline CI no levantó servicios.
- El proveedor de Grafana carga los dashboards desde config/grafana/dashboards/ automáticamente.

Para repetir las comprobaciones locales actuales, consulta los comandos en [Integración continua](ci.md), [Troubleshooting](troubleshooting.md), [scripts/validate.ps1](../scripts/validate.ps1) y [scripts/validate-container-exporter.ps1](../scripts/validate-container-exporter.ps1).

## GitHub Actions

La ejecución CI #4 asociada al PR #1, en el commit de rama a07bbb4 integrado en main como 56c5c87, pasó los cuatro jobs remotos:

1. Python 3.10 tests.
2. Python 3.13 tests.
3. Static quality and security checks.
4. Dashboard and monitoring configuration validation.

Esto es evidencia remota del código integrado en esa ejecución y no una validación de los cambios de documentación de esta rama. Los cambios de esta rama necesitan una nueva ejecución remota.

## Evidencia visual

Se incorporaron seis capturas PNG auténticas en [screenshots/](../screenshots/README.md): los dos dashboards de Grafana, Prometheus Targets, reglas de Prometheus, Alertmanager y GitHub Actions. Se inspeccionaron a resolución original, no se detectaron credenciales ni rutas personales, y las imágenes conservaron sus hashes SHA-256 al normalizar sus nombres. Son evidencia del instante de captura; no garantizan el estado actual del runtime.

Infrastructure Overview cuenta Prometheus y Node Exporter en el KPI 2/2. Prometheus Targets incluye además container-exporter y muestra 3/3. Las reglas están INACTIVE en la captura porque el sistema está normal; TestIncident permanece inactiva por diseño. Alertmanager no presenta grupos porque no había alertas disparadas en ese momento. La captura de GitHub Actions muestra la ejecución #4 con los cuatro jobs exitosos; los cambios documentales actuales aún necesitan una ejecución CI nueva.

## Repetición

Con Docker Desktop iniciado:

~~~powershell
docker compose config --quiet
docker compose ps
.\scripts\validate.ps1
~~~

Para el dashboard Docker, inicia el exporter manualmente y ejecuta:

~~~powershell
.\scripts\run-container-exporter.ps1
.\scripts\validate-container-exporter.ps1
~~~

El exporter usa el acceso Docker del usuario actual y no debe tratarse como una frontera de seguridad. Revisa [su documentación de seguridad y límites](container-exporter.md).
