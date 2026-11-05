# Integración continua

El workflow `.github/workflows/ci.yml` valida cambios de `main`, `develop`, `development`, `feature/**` y `bugfix/**`, pull requests dirigidos a `main` y ejecuciones manuales (`workflow_dispatch`). Usa permisos `contents: read`, acciones fijadas por SHA y no consume credenciales ni secretos locales. Los trabajos de pull request ejecutan el mismo conjunto de pruebas de solo lectura; no se usa `pull_request_target`.

## Qué ejecuta GitHub Actions

- Compila sintácticamente el código Python y ejecuta pruebas `unittest` de Incident Logger, Container Exporter, External Heartbeat y el supervisor de egreso en Python 3.10 y 3.13. Las pruebas del exporter simulan Docker CLI; las del heartbeat simulan respuestas HTTP; las del supervisor usan DNS sintético. Ninguna contacta Healthchecks.io.
- Instala las dependencias web desde el lockfile y ejecuta `pnpm check`, `pnpm build`, `pnpm test` y `pnpm audit --audit-level high` para el portal Astro y sus capturas estáticas.
- Comprueba sintaxis de scripts PowerShell sin ejecutarlos, y aplica Ruff a Python, yamllint a YAML y actionlint al workflow.
- Audita los paquetes fijados de herramientas CI con pip-audit. La aplicación no declara dependencias Python de terceros y ese supuesto se comprueba explícitamente; si se incorpora un manifiesto de runtime, el job falla hasta incluirlo en la auditoría.
- Revisa reglas `.gitignore`, evita versionar `.env`, `data/`, cachés y artefactos runtime, y escanea con Gitleaks los archivos Git rastreados y no ignorados que podrían entrar al cambio. No recorre objetos/historial Git; todos los hallazgos se redactan antes de aparecer en los logs. El escaneo excluye `.env` y reportes locales ignorados.
- Valida la estructura JSON de los tres dashboards, incluyendo sus nombres, UID, IDs de panel y los inventarios de 14, 12 y 13 paneles.
- Ejecuta `docker compose config --quiet` sobre la configuración predeterminada y los overrides opt-in de Telegram y External Heartbeat en una copia temporal con credenciales ficticias no sensibles. Solo renderiza configuración: no inicia contenedores, no envía pings, no contacta el Docker Engine ni cambia servicios o volúmenes.
- Comprueba documentación Markdown, enlaces locales y bloques Mermaid. Valida Blackbox Exporter, el enrutamiento opt-in de Telegram y las plantillas con destinos sintéticos; valida además la red, el montaje de secreto, los límites de recursos y la URL fija del agente External Heartbeat. Esas pruebas no contactan Telegram ni Healthchecks.io.
- Descarga `promtool` 3.15.0, `amtool` 0.34.1 y `actionlint` 1.7.12 desde releases exactos, verifica SHA-256 y valida Prometheus, reglas, rule tests, Alertmanager y el workflow.

El workflow se compone de cuatro jobs; la matriz Python genera dos resultados separados, por lo que GitHub muestra cinco check runs. La captura histórica de CI #4 y su ejecución asociada al PR #1 corresponden a una versión anterior del workflow, antes de incorporar las validaciones Astro.

Ruff usa el conjunto estable `E4,E7,E9,F` para todo Python con sintaxis objetivo Python 3.10; verifica formato solo en los nuevos helpers de CI para evitar reformatear masivamente código de fases anteriores. yamllint hereda el perfil relajado y desactiva el límite cosmético de longitud, ya que algunas anotaciones de reglas son deliberadamente largas.

El job de dependencias analiza los paquetes de CI definidos en `.github/requirements-ci.txt`; CI no realiza un escaneo integral de vulnerabilidades de las imágenes Docker (incluidas sus capas base) ni sustituye un escáner de imágenes. El archivo fija las versiones directas y transitivas de las herramientas. Los hashes de los artefactos PyPI no forman parte de esta fase.

## Comprobaciones locales

Desde la raíz del repositorio en PowerShell:

```powershell
python -m compileall -q services scripts
python -m unittest discover -s services/incident-logger -p 'test_*.py' -v
python -m unittest discover -s services/container-exporter -p 'test_*.py' -v
python -m unittest discover -s services/heartbeat-agent -p 'test_*.py' -v
python -m unittest discover -s services/heartbeat-egress-policy -p 'test_*.py' -v
python scripts/ci/validate_dashboards.py
python scripts/ci/validate_blackbox.py
python scripts/ci/check_runtime_dependencies.py
python scripts/ci/check_repo_hygiene.py
python scripts/ci/validate_compose.py
python scripts/ci/validate_telegram_alertmanager.py
python scripts/ci/validate_heartbeat.py
python scripts/ci/validate_cloud_docs.py
pwsh -NoProfile -File scripts/ci/validate-powershell.ps1
python -m pip install --disable-pip-version-check -r .github/requirements-ci.txt
ruff check services scripts --select E4,E7,E9,F --target-version py310
ruff format --check scripts/ci
yamllint .github config compose.heartbeat.yaml
pip-audit --strict --requirement .github/requirements-ci.txt
git diff --check
```

`validate_compose.py` copia solo el árbol del proyecto a una carpeta temporal, omite `.env` y datos locales, añade credenciales ficticias y elimina la copia al terminar. No lee, sustituye ni elimina el `.env` real.

Las pruebas automáticas del External Heartbeat inspeccionan el override opt-in y ejercitan transporte HTTP simulado y DNS sintético. No requieren credenciales ni contactan al proveedor; por sí solas no verifican conectividad externa ni entrega de notificaciones. La cronología operativa corregida por el propietario separa tres ciclos aceptados durante la validación inicial, cuando todavía se utilizaba la credencial original, de cuatro ciclos posteriores al reemplazo y recreación del agente. El propietario confirmó el estado del nuevo check, la transición del original a `Down` y su posterior eliminación; la hora de eliminación no fue proporcionada. La inspección Docker inicial fue anterior al reemplazo y no demuestra el estado actual. Todo ello es evidencia histórica, no una prueba de CI, monitorización en vivo ni garantía de disponibilidad. Consulte el [diseño de seguridad y evidencia](external-heartbeat.md) y el [runbook](runbooks/external-heartbeat.md).

Para repetir las validaciones de métricas, instala o localiza `promtool` 3.15.0 y `amtool` 0.34.1 y ejecuta:

```powershell
python scripts/ci/validate_prometheus.py --promtool C:\ruta\a\promtool.exe
amtool check-config config/alertmanager/alertmanager.yml
actionlint .github/workflows/ci.yml
python scripts/ci/scan_secrets.py --gitleaks C:\ruta\a\gitleaks.exe
```

También se recomienda `docker compose ps`, `docker compose config --quiet`, `scripts/validate.ps1` y, con el exporter local iniciado, `scripts/validate-container-exporter.ps1` para comprobar el runtime de Windows/Docker Desktop. Estas verificaciones requieren la infraestructura y las credenciales locales del usuario y se mantienen separadas de CI.

## Alcance y límites

El runner Ubuntu no representa Docker Desktop/WSL2 ni el Windows local. CI no ejecuta el exporter contra el daemon del runner, no inicia servicios Compose y no demuestra acceso real por `host.docker.internal`. Tampoco valida paneles mediante la API autenticada de Grafana ni consulta métricas vivas; la validación JSON confirma estructura e inventario, no que cada PromQL esté devolviendo muestras. Usa las comprobaciones locales anteriores para validar targets, datasource, salud de los servicios Compose y datos actuales.

## Evidencia histórica de GitHub Actions

La [ejecución CI #4 asociada al PR #1](https://github.com/charrixxz23/it-infrastructure-monitoring-center/actions/runs/40109656359), en el commit de rama `a07bbb4` que se integró en `main` como `56c5c87`, concluyó con éxito en los cuatro check runs que tenía entonces: Python 3.10 tests, Python 3.13 tests, Static quality and security checks, y Dashboard and monitoring configuration validation. Esa ejecución histórica no incluye el job Astro incorporado después. Los cambios actuales requieren su propia ejecución remota; no se presentan los comandos locales como resultado de GitHub Actions.

## Interpretación de fallos

- `unittest` o `compileall`: revisar lógica, imports o compatibilidad entre las versiones Python probadas.
- Ruff/yamllint/actionlint/PowerShell parser: corregir formato o sintaxis en el archivo indicado, sin aplicar auto-fix ciego en CI.
- Dashboard JSON: revisar título, UID, IDs y queries de los archivos provisionados.
- Compose config: revisar `compose.yaml`, rutas referenciadas y nombres de variables; este error sucede antes de levantar servicios.
- promtool/amtool: revisar YAML, PromQL, rutas de reglas, expectativas de tests o configuración de notificaciones.
- pip-audit: revisar advisory y versiones afectadas antes de actualizar una herramienta CI; no se aplica ninguna actualización automática.
- Gitleaks o higiene: no copiar valores detectados a issues/logs. Confirmar si el patrón es un secreto real; si lo es, retirarlo del historial de Git y rotarlo. Una excepción por falso positivo debe ser precisa y documentada.

## Badge

El README usa el badge del workflow existente en el repositorio:

```markdown
[![CI](https://github.com/charrixxz23/it-infrastructure-monitoring-center/actions/workflows/ci.yml/badge.svg)](https://github.com/charrixxz23/it-infrastructure-monitoring-center/actions/workflows/ci.yml)
```

El badge refleja el estado que GitHub informa para la rama predeterminada. No demuestra que los cambios locales o una rama que aún no se ha enviado hayan pasado CI.

## Fuentes de las versiones fijadas

- Acciones: [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) y [setup-python v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0); el workflow fija sus commits completos.
- Herramientas CI: [Ruff 0.17.2](https://github.com/astral-sh/ruff/releases/tag/0.17.2), [yamllint 1.38.0](https://github.com/adrienverge/yamllint/releases/tag/v1.38.0), [pip-audit 2.10.1](https://pypi.org/project/pip-audit/2.10.1/), [Gitleaks 8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1) y [actionlint 1.7.12](https://github.com/rhysd/actionlint/releases/tag/v1.7.12).
- Configuración de monitoreo, alineada con las versiones ya fijadas en Compose: [Prometheus 3.15.0](https://github.com/prometheus/prometheus/releases/tag/v3.15.0) y [Alertmanager 0.34.1](https://github.com/prometheus/alertmanager/releases/tag/v0.34.1).
