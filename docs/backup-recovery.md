# Backup and Disaster Recovery — Fase 10.1

## Alcance

Esta iteración añade inventario de solo lectura, planificación y un respaldo explícito de archivos versionados. No crea snapshots de volúmenes, no detiene ni reinicia servicios y no es un respaldo integral del sistema. El artefacto de configuración describe el `HEAD` confirmado, no el working tree.

Los nombres declarados en `compose.yaml` para los volúmenes persistentes son:

| Recurso | Nombre Docker | Contenido esperado | Estrategia consistente |
|---|---|---|---|
| Prometheus | `itmc-prometheus-data` | TSDB; retención de 15 días / 5 GB | Pendiente. No archivar TSDB/WAL activos como snapshot. La API de snapshot administrativa sigue deshabilitada. Se requiere mantenimiento planificado o snapshot de almacenamiento compatible, seguido por una restauración de ensayo. |
| Grafana | `itmc-grafana-data` | Base SQLite y estado de Grafana | Pendiente. SQLite Online Backup API es la opción a evaluar; no copiar directamente el archivo SQLite activo. También falta verificar plugins/otros archivos y restauración completa con Grafana 13.2.4. |
| Alertmanager | `itmc-alertmanager-data` | Estado de alertas/silencios; retención de 120 horas | Pendiente. No existe en esta fase una exportación consistente probada; definir ventana controlada o mecanismo soportado por versión y ensayar restauración aislada. |
| Incident Logger | `itmc-incident-log-data` | JSONL rotado de incidentes | Pendiente. Para una copia coherente hay que evitar la rotación/escritura concurrente, validar orden y límites de los archivos, y ensayar su lectura/restauración. |

El Compose conserva nombres fijos de volúmenes y la red `itmc-monitoring`. No se renombraron recursos de producción para esta fase.

## Inventario y planificación

Desde PowerShell en la raíz del repositorio:

```powershell
.\scripts\inventory-itmc.ps1
.\scripts\backup-itmc.ps1
```

`inventory-itmc.ps1` consulta `docker info`, inspecciona únicamente los cuatro nombres de volumen esperados y filtra contenedores por la etiqueta del proyecto Compose. Solo muestra nombres de volumen, servicio, estado y salud resumida. No imprime mountpoints, variables de entorno, logs, identificadores de contenedor, nombres de targets privados ni contenido de volúmenes. `docker volume inspect` no ofrece de forma portable bytes usados; esa cifra aparece como no disponible. El espacio libre se comprueba para el destino de un futuro respaldo, no para el contenido interno de cada volumen.

El segundo comando usa `Plan` por defecto. Lee Git y metadatos de Docker; no escribe archivos, consulta los datos dentro de volúmenes, crea snapshots ni cambia contenedores. Si Docker CLI/Engine falta o no responde, indica que no pudo inspeccionar recursos; no interpreta “no inspeccionado” como “ausente”.

**Estado de la verificación de esta iteración (2026-11-05):** Docker CLI no pudo conectarse al Docker Engine de Docker Desktop. Por eso, la existencia/disponibilidad actual de los cuatro volúmenes y el inventario runtime permanecen sin verificar. Los nombres anteriores se confirman solo como declaración versionada de Compose. No se iniciaron servicios ni se modificaron volúmenes.

## Respaldo de configuración versionada

El modo `BackupConfig` es una operación separada y requiere autorización explícita del operador:

```powershell
.\scripts\backup-itmc.ps1 -Mode BackupConfig `
  -Destination 'E:\SecureBackups\ITMC' `
  -ConfirmEncryptedDestination
```

Condiciones:

- El destino debe existir, ser absoluto, local, escribible y estar fuera del repositorio, de directorios sensibles y de carpetas conocidas de sincronización. El script requiere espacio libre estimado y no sobrescribe un archivo existente.
- `-ConfirmEncryptedDestination` es una declaración del operador, **no una medición del cifrado**. Verifica BitLocker/cifrado del medio por el procedimiento administrado de la organización. No es posible detectar todos los sistemas de sincronización en la nube.
- El working tree debe estar limpio. Se usa `git cat-file` para leer solo objetos versionados en `HEAD`; no se empaquetan cambios locales sin commit.
- El ZIP contiene configuración, código, CI, documentación seleccionada y `.env.example`, con un manifiesto JSON interno: UTC, commit, formato, alcance y SHA-256/tamaño por archivo. El ZIP temporal se vuelve a verificar y se publica atómicamente sin reemplazar un archivo existente; el sistema de archivos de destino debe admitir enlaces duros locales.
- Se excluyen `.env`, `.secrets/`, `data/`, el target doméstico local, bases/logs/runtime, cachés, artefactos de web y `docs/audit-report.md`. El artefacto puede revelar arquitectura/configuración interna: mantenlo cifrado y con acceso restringido.
- La verificación local no prueba autenticidad del firmante: el manifiesto y los hashes detectan corrupción o modificaciones accidentales, pero no sustituyen una firma ni una copia externa inmutable.

Verifica un artefacto manualmente, sin leer su contenido en pantalla:

```powershell
.\scripts\backup-itmc.ps1 -Mode Verify `
  -Archive 'E:\SecureBackups\ITMC\itmc-config-<timestamp>-<commit>.zip'
```

La herramienta no sube artefactos. Está prohibido añadirlos a Git, GitHub Actions, issues/PR, Cloudflare Pages o respaldos sin cifrar.

## Credenciales y archivos locales

`.env`, `.secrets/`, el override de targets domésticos y otros archivos ignorados no son parte de `BackupConfig`. No uses `git add -f` ni los copies a este ZIP. Su custodia es un procedimiento separado: usa un gestor de secretos cifrado aprobado o un medio local cifrado con la clave de recuperación custodiada por separado. No guardes la clave junto al archivo cifrado; limita ACL con revisión que conserve SYSTEM, Administrators, propietario y el acceso necesario de Docker Desktop. Restaura en un directorio temporal protegido para comprobar legibilidad, evita mostrar valores y elimina de forma segura las copias temporales según la política aprobada. Esta fase no automatiza ni prueba la custodia cifrada de secretos.

## Restauración: estado y límites

La restauración de los cuatro volúmenes aún no está implementada ni probada. No extraigas un respaldo directamente en los volúmenes actuales. Antes de aceptar una recuperación habrá que:

1. Validar el manifiesto y hashes en almacenamiento local cifrado y fijar una copia de trabajo aislada.
2. Crear una configuración de ensayo derivada de un checkout limpio; no copiar el working tree del host.
3. Cambiar **todos** los nombres fijos de volúmenes, incluidas las cuatro declaraciones `name:`, y usar una red aislada con nombre exclusivo en lugar de `itmc-monitoring`.
4. Eliminar/reasignar publicaciones de puertos para evitar colisiones. No conectar el ensayo a redes de producción ni montar sus volúmenes en escritura.
5. Deshabilitar rutas Telegram, External Heartbeat, Blackbox/destinos domésticos y cualquier otro envío externo. Usar configuración sintética/sink local; no enviar pings, alertas o notificaciones reales.
6. Aplicar solo una estrategia consistente aprobada para cada servicio, validar logs/health y datos, y registrar qué se recuperó. La prueba debe permanecer aislada y no demuestra disponibilidad de producción.

En Docker Desktop/WSL2 el backend de volúmenes está administrado por Docker; no se debe asumir que el mountpoint de Linux es un path de Windows o que una copia de archivos del host conserva permisos, ownership, WAL o consistencia. Mantén Prometheus `--web.enable-admin-api` deshabilitado. La recuperación reproducible de volúmenes queda para fases posteriores, tras escoger un procedimiento soportado por versión y autorizar una prueba aislada.

## CI

CI ejecuta pruebas con un repositorio Git temporal, respuestas Docker simuladas, fallos de copia y archivos sintéticos. No requiere Docker Engine, no accede a volúmenes reales, no lee secretos, no crea respaldos de producción y no presenta una restauración sintética como evidencia operativa. La CI existente de configuraciones, Gitleaks, Ruff, tests y Astro se conserva. CI no realiza un escaneo integral de vulnerabilidades de imágenes Docker.
