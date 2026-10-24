# Integración continua

El workflow `.github/workflows/ci.yml` valida cambios de `main`, `develop`, `development`, `feature/**` y `bugfix/**`, pull requests dirigidos a `main` y ejecuciones manuales (`workflow_dispatch`). Usa permisos `contents: read`, acciones fijadas por SHA y no consume credenciales ni secretos locales. Los trabajos de pull request ejecutan el mismo conjunto de pruebas de solo lectura; no se usa `pull_request_target`.

## Qué ejecuta GitHub Actions

- Compila sintácticamente el código Python y ejecuta las pruebas `unittest` de Incident Logger y Container Exporter en Python 3.10 y 3.13. Las pruebas del exporter simulan Docker CLI y no consultan el daemon.
- Comprueba sintaxis de scripts PowerShell sin ejecutarlos, y aplica Ruff a Python, yamllint a YAML y actionlint al workflow.
- Audita los paquetes fijados de herramientas CI con pip-audit. La aplicación no declara dependencias Python de terceros y ese supuesto se comprueba explícitamente; si se incorpora un manifiesto de runtime, el job falla hasta incluirlo en la auditoría.
- Revisa reglas `.gitignore`, evita versionar `.env`, `data/`, cachés y artefactos runtime, y escanea con Gitleaks los archivos Git rastreados y no ignorados que podrían entrar al cambio. No recorre objetos/historial Git; todos los hallazgos se redactan antes de aparecer en los logs. El escaneo excluye `.env` y reportes locales ignorados.
- Valida la estructura JSON de ambos dashboards, incluyendo sus nombres, UID, IDs de panel y el inventario esperado de 14 y 12 paneles.
- Ejecuta `docker compose config --quiet` en una copia temporal con un `.env` efímero no sensible. Solo renderiza la configuración: no inicia contenedores, no contacta el Docker Engine ni cambia servicios o volúmenes.
- Descarga `promtool` 3.15.0, `amtool` 0.34.1 y `actionlint` 1.7.12 desde releases exactos, verifica SHA-256 y valida Prometheus, reglas, rule tests, Alertmanager y el workflow.

Ruff usa el conjunto estable `E4,E7,E9,F` para todo Python con sintaxis objetivo Python 3.10; verifica formato solo en los nuevos helpers de CI para evitar reformatear masivamente código de fases anteriores. yamllint hereda el perfil relajado y desactiva el límite cosmético de longitud, ya que algunas anotaciones de reglas son deliberadamente largas.

El job de dependencias analiza los paquetes de CI definidos en `.github/requirements-ci.txt`; no escanea las capas base de imágenes Docker ni sustituye un escáner de imágenes. El archivo fija las versiones directas y transitivas de las herramientas. Los hashes de los artefactos PyPI no forman parte de esta fase.

## Comprobaciones locales

Desde la raíz del repositorio en PowerShell:

```powershell
python -m compileall -q services scripts
python -m unittest discover -s services/incident-logger -p 'test_*.py' -v
python -m unittest discover -s services/container-exporter -p 'test_*.py' -v
python scripts/ci/validate_dashboards.py
python scripts/ci/check_runtime_dependencies.py
python scripts/ci/check_repo_hygiene.py
python scripts/ci/validate_compose.py
pwsh -NoProfile -File scripts/ci/validate-powershell.ps1
python -m pip install --disable-pip-version-check -r .github/requirements-ci.txt
ruff check services scripts --select E4,E7,E9,F --target-version py310
ruff format --check scripts/ci
yamllint .github config
pip-audit --strict --requirement .github/requirements-ci.txt
git diff --check
```

`validate_compose.py` copia solo el árbol del proyecto a una carpeta temporal, omite `.env` y datos locales, añade credenciales ficticias y elimina la copia al terminar. No lee, sustituye ni elimina el `.env` real.

Para repetir las validaciones de métricas, instala o localiza `promtool` 3.15.0 y `amtool` 0.34.1 y ejecuta:

```powershell
python scripts/ci/validate_prometheus.py --promtool C:\ruta\a\promtool.exe
amtool check-config config/alertmanager/alertmanager.yml
actionlint .github/workflows/ci.yml
python scripts/ci/scan_secrets.py --gitleaks C:\ruta\a\gitleaks.exe
```

También se recomienda `docker compose ps`, `docker compose config --quiet`, `scripts/validate.ps1` y, con el exporter local iniciado, `scripts/validate-container-exporter.ps1` para comprobar el runtime de Windows/Docker Desktop. Estas verificaciones requieren la infraestructura y las credenciales locales del usuario y se mantienen separadas de CI.

## Alcance y límites

El runner Ubuntu no representa Docker Desktop/WSL2 ni el Windows local. CI no ejecuta el exporter contra el daemon del runner, no inicia servicios Compose y no demuestra acceso real por `host.docker.internal`. Tampoco valida paneles mediante la API autenticada de Grafana ni consulta métricas vivas; la validación JSON confirma estructura e inventario, no que cada PromQL esté devolviendo muestras. Usa las comprobaciones locales anteriores para validar targets, datasource, salud de los cinco servicios y datos actuales.

## Interpretación de fallos

- `unittest` o `compileall`: revisar lógica, imports o compatibilidad entre las versiones Python probadas.
- Ruff/yamllint/actionlint/PowerShell parser: corregir formato o sintaxis en el archivo indicado, sin aplicar auto-fix ciego en CI.
- Dashboard JSON: revisar título, UID, IDs y queries de los archivos provisionados.
- Compose config: revisar `compose.yaml`, rutas referenciadas y nombres de variables; este error sucede antes de levantar servicios.
- promtool/amtool: revisar YAML, PromQL, rutas de reglas, expectativas de tests o configuración de notificaciones.
- pip-audit: revisar advisory y versiones afectadas antes de actualizar una herramienta CI; no se aplica ninguna actualización automática.
- Gitleaks o higiene: no copiar valores detectados a issues/logs. Confirmar si el patrón es un secreto real; si lo es, retirarlo del historial de Git y rotarlo. Una excepción por falso positivo debe ser precisa y documentada.

## Badge

Cuando se conozca el remoto de GitHub, añadir el badge con su propietario y nombre reales:

```text
https://github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg
```

El repositorio local no permite inferir esa URL y no se configura una ficticia. Añadir el workflow no equivale a una ejecución remota: hasta que se suba la rama y GitHub Actions lo ejecute, el estado remoto queda pendiente.

## Fuentes de las versiones fijadas

- Acciones: [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) y [setup-python v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0); el workflow fija sus commits completos.
- Herramientas CI: [Ruff 0.17.2](https://github.com/astral-sh/ruff/releases/tag/0.17.2), [yamllint 1.38.0](https://github.com/adrienverge/yamllint/releases/tag/v1.38.0), [pip-audit 2.10.1](https://pypi.org/project/pip-audit/2.10.1/), [Gitleaks 8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1) y [actionlint 1.7.12](https://github.com/rhysd/actionlint/releases/tag/v1.7.12).
- Configuración de monitoreo, alineada con las versiones ya fijadas en Compose: [Prometheus 3.15.0](https://github.com/prometheus/prometheus/releases/tag/v3.15.0) y [Alertmanager 0.34.1](https://github.com/prometheus/alertmanager/releases/tag/v0.34.1).
