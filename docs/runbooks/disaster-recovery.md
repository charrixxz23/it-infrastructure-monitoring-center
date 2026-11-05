# Runbook — Disaster Recovery de ITMC

## Estado de este runbook

Fase 10.1 solo ofrece inventario Docker de metadatos, un plan de solo lectura por defecto, y respaldo explícito de la configuración versionada. No es una guía para recuperar servicios de producción hoy. No detengas ni reinicies el stack, no leas los archivos secretos y no modifiques volúmenes durante una revisión rutinaria.

Los nombres de volúmenes declarados en Compose están en [backup-recovery.md](../backup-recovery.md). Su presencia no se pudo verificar en la implementación de esta fase porque Docker Engine no estaba disponible. No deduzcas que existan a partir del Compose.

## Inventario previo (solo lectura)

En PowerShell, desde el checkout:

```powershell
.scripts\inventory-itmc.ps1
.scripts\backup-itmc.ps1
```

El primer comando inspecciona solo los metadatos permitidos por el helper. El segundo usa `Plan`; no crea archivos ni toca volúmenes. Registra el resultado por canal interno aprobado y redacta cualquier detalle de host; no guardes logs crudos en GitHub/PR. Si el Engine no está disponible, la salida será `not_inspected` y la verificación queda pendiente. No intentes iniciar Docker Desktop como parte de una auditoría que no lo autoriza.

## Respaldo de configuración

1. Selecciona un directorio local ya existente, fuera del checkout y de directorios sincronizados, con cifrado de disco/medio verificado y espacio suficiente. No uses red compartida para este modo.
2. Confirma `git status --short --branch` limpio. El backup solo corresponde al commit `HEAD`; no incluye edición sin commit.
3. Ejecuta el plan (`scripts/backup-itmc.ps1` sin parámetros) y revisa el alcance, exclusiones y capacidad estimada.
4. Solo tras confirmar destino cifrado, ejecuta:

   ```powershell
   .\scripts\backup-itmc.ps1 -Mode BackupConfig `
     -Destination 'E:\SecureBackups\ITMC' `
     -ConfirmEncryptedDestination
   ```

5. El script crea un ZIP temporal dentro del destino, verifica el manifiesto y los SHA-256, y publica el ZIP completo de forma atómica sin reemplazar archivos existentes. El sistema de archivos debe admitir enlaces duros locales. Ante fallo o interrupción elimina únicamente su temporal. No limpia directorios, no borra copias anteriores ni modifica volúmenes.
6. Verifica el archivo sin imprimir su contenido:

   ```powershell
   .\scripts\backup-itmc.ps1 -Mode Verify `
     -Archive 'E:\SecureBackups\ITMC\itmc-config-<timestamp>-<commit>.zip'
   ```

7. Conserva el ZIP cifrado fuera del repositorio con control de acceso y política de retención aprobados. No lo cargues a GitHub, CI, Cloudflare, chat ni tickets.

El switch `-ConfirmEncryptedDestination` es una declaración del operador: el script no detecta BitLocker, cifrado de hardware ni todos los proveedores de sincronización. El ZIP solo incluye objetos versionados y permitidos de `HEAD`; no contiene `.env`, `.secrets/`, el target doméstico local, `data/`, logs, bases de datos, volúmenes Docker ni el informe de auditoría.

## Estado de consistencia de volúmenes

No se ejecuta backup de volumen en esta fase. Antes de automatizar o copiar datos:

- **Prometheus:** no archives TSDB/WAL activos como copia consistente. La API administrativa de snapshot permanece deshabilitada. Evaluar una ventana de mantenimiento con cierre ordenado y volumen quieto o una capacidad de snapshot compatible con el almacenamiento Docker Desktop; validar restauración, WAL y retención con la versión del proyecto.
- **Grafana:** no copies el archivo SQLite activo directamente. Evaluar SQLite Online Backup API, respetando WAL/permisos, y determinar si plugin state u otros ficheros del volumen requieren una estrategia adicional. Validar con Grafana 13.2.4.
- **Alertmanager:** el estado persistido no tiene exportación/restore probada en esta fase. Diseñar copia bajo mantenimiento o mecanismo soportado y comprobar silencios/notificaciones en un entorno aislado.
- **Incident Logger:** los archivos JSONL pueden rotar durante una copia. Quiesce controlado, consistencia de los archivos rotados y lectura tras recuperación deben probarse antes de calificarlo como respaldo válido.

Cada método seguirá marcado pendiente hasta que se ejecute en entorno de ensayo con una restauración verificable. No habilites `--web.enable-admin-api` como atajo para snapshots. No crees un contenedor helper con permisos extra o montajes de producción en escritura sin diseño y revisión separados.

## Plan de restauración aislada futura

Requiere autorización de mantenimiento y un cambio/fase independiente. No ejecutar como recuperación de producción sin revisión.

1. Verifica integridad del ZIP con `Mode Verify` en almacenamiento local cifrado.
2. Extrae una copia de ensayo en un directorio temporal restringido. No extraigas encima del checkout/Compose activo.
3. Prepara un Compose de ensayo revisado manualmente: asigna nombres únicos a **cada** volumen (Compose actual usa nombres fijos `itmc-*`), sustituye la red fija `itmc-monitoring` por una red exclusiva, y cambia o elimina publicaciones de puertos `127.0.0.1` para evitar colisiones.
4. No montes volúmenes de producción. Crea volúmenes vacíos con nombre exclusivo para la recuperación de prueba y carga solo una copia consistente validada. No uses `down -v`, `volume rm` o limpieza global en ninguna etapa.
5. Usa `.env` sintético, targets de documentación y notificaciones redirigidas a sinks locales. Omite los overrides Telegram/External Heartbeat. No habilites Blackbox a destinos reales ni envíes pings externos.
6. Arranca únicamente el proyecto aislado tras revisar el plan y comprueba health, datos esperados, consistencia temporal y logs sin secretos.
7. Documenta el commit, checksums, método por volumen, limitaciones y resultados. Una restauración sintética no demuestra una restauración real hasta que se usen datos representativos y el propietario autorice esa prueba.

El Compose existente tiene la red `itmc-monitoring` fija y puertos enlazados al loopback del host. Cambiar solo el nombre de proyecto no es suficiente para evitar colisión de esos recursos.

## Secretos y privacidad

Credenciales de `.env`, `.secrets/`, bot tokens/chat IDs y targets locales requieren una copia separada, cifrada y bajo control de acceso. Use un gestor cifrado aprobado o almacenamiento offline cifrado; custodia la clave de recuperación por separado y prueba la recuperación en un área temporal protegida. No incluyas estos datos en el ZIP de configuración, Git, CI, Cloudflare, capturas o documentación. Esta fase no automatiza el respaldo de secretos ni prueba su restauración.

## Reversión y límites

Los comandos `Plan`, `Inventory` y `Verify` son de lectura. `BackupConfig` solo escribe un ZIP local nuevo y un temporal propio; no reemplaza configuración, no escribe volúmenes ni administra contenedores. Si el archivo no aparece tras un fallo, conserva los datos del destino y revisa permisos/espacio por el procedimiento local aprobado; el script no realiza limpieza adicional.

Las pruebas automatizadas usan fixtures Git y respuestas Docker simuladas. No acceden a Docker Engine real ni prueban datos de producción. Windows, Docker Desktop/WSL2, el sistema de archivos y el destino cifrado permanecen dependencias del plan de recuperación.
