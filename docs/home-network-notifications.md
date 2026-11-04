# Home Network Monitoring — Notificaciones

## Estado y arquitectura

La integración está implementada y fue validada localmente mediante una alerta sintética FIRING → RESOLVED. Permanece opt-in para instalaciones nuevas: la configuración predeterminada `config/alertmanager/alertmanager.yml` no requiere secretos ni envía Telegram y sigue enviando FIRING y RESOLVED al Incident Logger. El detalle de la prueba está en el [informe de validación de Fase 8](phase-8-telegram-validation.md).

Los archivos `config/alertmanager/alertmanager.telegram.yml`, la plantilla y `compose.telegram.yaml` forman una configuración alternativa explícita. Solo se selecciona al ejecutar Compose con ambos archivos `-f`; el comando predeterminado no carga esa configuración ni monta secretos. El override exige que existan ambos archivos locales debido a `bind.create_host_path: false`, para evitar crear directorios vacíos que oculten un error de configuración.

Alertmanager `v0.34.1` admite la integración nativa `telegram_configs`, `bot_token_file`, `chat_id_file`, `send_resolved` y el modo de texto plano (`parse_mode: ""`). Se usan rutas de archivo en lugar de interpolar credenciales en YAML o variables de entorno. [Configuración oficial v0.34.1](https://github.com/prometheus/alertmanager/blob/v0.34.1/docs/configuration.md#telegram_config) · [Release v0.34.1](https://github.com/prometheus/alertmanager/releases/tag/v0.34.1)

El archivo optativo contiene una allowlist por `source="home-network"` y los siete nombres de alerta existentes: `BlackboxExporterDown`, `BlackboxExporterTargetMissing`, `HomeProbeExecutionError`, `HomeRouterTargetMissing`, `HomeRouterUnavailable`, `HomeDNSResolutionUnavailable` y `HomeInternetHTTPSUnavailable`. Un primer hijo entrega a Telegram con espera de 30 segundos; `continue: true` permite que el siguiente hijo conserve la entrega existente al Incident Logger con su espera original de 3 segundos. Cualquier otra alerta sigue el receptor raíz `incident-logger` y no se envía a Telegram.

La plantilla solo muestra el estado, nombre de regla, severidad y resumen genérico. No incluye `instance`, direcciones, etiquetas de destino ni metadatos del hogar. El texto se envía en modo plano, evitando interpretación HTML/Markdown.

## Secretos y privacidad

En el equipo que realizó la prueba, los dos valores locales se guardan en archivos separados bajo `.secrets/`, carpeta ignorada por Git. No se publican ni se incluyen en el informe:

- `.secrets/telegram-bot-token`
- `.secrets/telegram-chat-id`

No añadas esos archivos, sus valores, capturas ni copias a Git. El bot token permite controlar el bot; el chat ID es un identificador privado aunque no sea una contraseña. Los montajes de override son de solo lectura. Esto reduce modificaciones desde el contenedor, pero no vuelve inocuo el proceso: Docker Desktop y el proceso de Alertmanager pueden leer ambos archivos. Protege la cuenta Windows y el directorio local con ACL restrictivas. No imprimas variables, contenidos de archivos ni la configuración renderizada cuando contenga valores privados.

### Configuración local, solo cuando se autorice la activación

1. Crea el bot con [BotFather](https://t.me/BotFather) desde la cuenta Telegram autorizada. Copia el token directamente a un gestor de secretos temporal; no lo pegues en una orden de terminal, `.env`, YAML, ticket o chat.
2. Inicia un chat privado con el bot y envía `/start`. Obtén el ID de chat por un método confiable y local. No uses bots de terceros para descubrirlo. Antes de activar, confirma que el destino es el chat privado esperado y que el destinatario aprobó recibir las alertas.
3. Desde la raíz del repositorio, en PowerShell, crea los archivos sin mostrar su contenido en pantalla. Ejecuta la captura de forma interactiva y no uses valores como argumentos:

   ```powershell
   $secretDir = Join-Path (Get-Location) '.secrets'
   New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
   $tokenSecure = Read-Host 'Telegram bot token' -AsSecureString
   $tokenBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($tokenSecure)
   try {
       $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenBstr)
       [IO.File]::WriteAllText((Join-Path $secretDir 'telegram-bot-token'), $token, [Text.UTF8Encoding]::new($false))
   } finally {
       [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenBstr)
       Remove-Variable token -ErrorAction SilentlyContinue
       Remove-Variable tokenSecure -ErrorAction SilentlyContinue
   }
   $chatSecure = Read-Host 'Telegram chat ID' -AsSecureString
   $chatBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($chatSecure)
   try {
       $chat = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($chatBstr)
       [IO.File]::WriteAllText((Join-Path $secretDir 'telegram-chat-id'), $chat, [Text.UTF8Encoding]::new($false))
   } finally {
       [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($chatBstr)
       Remove-Variable chat -ErrorAction SilentlyContinue
       Remove-Variable chatSecure -ErrorAction SilentlyContinue
   }
   ```

   `SecureString` evita el eco al escribir, pero la conversión transitoria y el archivo final existen en texto claro en memoria/en disco. Limita ACL del directorio al usuario autorizado y a las cuentas necesarias para Docker Desktop. No sincronices la carpeta con almacenamiento en nube.
4. Comprueba solo la presencia y tamaño de ambos archivos, nunca su contenido; verifica que Git los ignora con `git check-ignore -v .secrets/telegram-bot-token .secrets/telegram-chat-id`.
5. Revisa la configuración alternativa sin iniciar servicios: `docker compose -f compose.yaml -f compose.telegram.yaml config --quiet`. El comando no imprime la configuración resuelta.
6. Después de aprobar la recepción, revisa primero que los servicios requeridos estén saludables y activa manualmente en una ventana de mantenimiento con `docker compose -f compose.yaml -f compose.telegram.yaml up -d alertmanager`. Compose puede iniciar dependencias detenidas y reemplazará Alertmanager para aplicar el archivo alternativo; puede producir una interrupción breve. No la ejecutes durante esta preparación. No hay recarga en vivo en este procedimiento.

### Evidencia de la prueba real local

La validación local ya envió una alerta sintética `HomeRouterUnavailable` con `source=home-network`, `severity=warning` e `instance=telegram-delivery-test`. Telegram recibió FIRING y RESOLVED, la alerta dejó de estar activa y el Incident Logger respondió HTTP 200 a ambos webhooks y conservó dos registros en su JSONL local. La evidencia de la primera prueba muestra 08:19 y 08:24 del 4 de noviembre de 2026, hora de Panamá; el intervalo visible es compatible con `group_interval: 5m`, pero no es una garantía de entrega.

Para repetir una prueba en el futuro, requiere autorización del destinatario y Alertmanager opt-in activo. Envía solo una alerta sintética claramente marcada y con las mismas etiquetas para la resolución; permite la ventana de agrupación, confirma FIRING y RESOLVED en ambos receptores, y asegúrate de que la alerta ya no esté activa. Nunca generes la prueba desconectando Internet ni uses direcciones domésticas en las etiquetas o anotaciones.

### Fallos, reintentos y recuperación

- Telegram requiere salida HTTPS a `api.telegram.org`; no se abre ningún puerto entrante, no se configura port forwarding ni UPnP.
- Si se pierde todo el ISP, Alertmanager no puede entregar mensajes externos. Puede reintentar mientras el proceso y la alerta sigan activos, pero la notificación no queda garantizada ni reemplaza el historial local. El Incident Logger sigue recibiendo eventos por la red interna si sus servicios están funcionando.
- Alertmanager agrupa y deduplica por `alertname`, `instance`, `mountpoint` y `source`. La espera Telegram es 30 s, el intervalo de grupo 5 min y el recordatorio 4 h. La espera aplaza avisos breves; las alertas resueltas dentro de ese período pueden no originar un mensaje de FIRING. Los mensajes RESOLVED se habilitan con `send_resolved: true`. Alertmanager 0.34.1 no ofrece un `timeout` específico en `telegram_configs`; el contexto de notificación queda acotado por `group_interval`.
- Al reiniciar Alertmanager, el estado de notificación usa el volumen existente `alertmanager_data`; esto no garantiza la entrega si Telegram o el equipo estuvieron inaccesibles.
- Un timeout o error de API se reporta por Alertmanager y su lógica de reintentos; nunca debe copiarse el mensaje de error completo a un informe sin comprobar que no contenga datos privados.

### Reversión y token comprometido

1. Para volver al modo estable, ejecuta la configuración predeterminada en la siguiente operación de mantenimiento: `docker compose -f compose.yaml up -d alertmanager`. No hagas esta operación como parte de la preparación, porque reemplaza el contenedor.
2. Revoca inmediatamente el token en BotFather si sospechas exposición; crear un token nuevo no borra copias anteriores. Luego elimina el archivo local con un método aprobado, revisa Git, backups y capturas, vuelve a crear el archivo con el nuevo secreto y repite la activación controlada.
3. Si hubo publicación accidental, elimina/revoca el token de inmediato y sigue el procedimiento de respuesta a secretos de GitHub; borrar el archivo de la rama actual no lo elimina del historial.

## Validación automatizada y estado actual

- `amtool check-config config/alertmanager/alertmanager.yml`
- `amtool check-config config/alertmanager/alertmanager.telegram.yml`
- `amtool config routes test --config.file=config/alertmanager/alertmanager.telegram.yml --verify.receivers=telegram-home-network,incident-logger alertname=HomeRouterUnavailable source=home-network severity=critical`
- Ejecuta pruebas equivalentes para cada alerta de la allowlist y una alerta no doméstica. La última solo debe elegir `incident-logger`.
- `docker compose -f compose.yaml config --quiet` valida el modo predeterminado sin iniciar servicios. Para validar el override, ambos archivos locales deben existir; nunca se guardan secretos ficticios en el repositorio.
- `scripts/ci/validate_telegram_alertmanager.py` y las pruebas de enrutamiento/plantilla validan la configuración sin hacer solicitudes a Telegram. La validación CI no usa credenciales.
- La prueba local descrita arriba sí contactó Telegram con una alerta sintética autorizada; no se ha repetido como parte del cambio documental.

La configuración estable predeterminada sigue disponible para instalaciones nuevas. Activar o revertir el override reemplaza Alertmanager y debe hacerse de forma controlada; esta documentación no cambia la configuración en ejecución ni los contenedores.
