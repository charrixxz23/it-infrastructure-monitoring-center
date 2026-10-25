# Home Network Monitoring — Notificaciones

## Estado

No se configuró Telegram, no se solicitó un token y no se almacenaron credenciales. Alertmanager mantiene únicamente su receiver existente hacia Incident Logger. Las reglas domésticas usan etiquetas genéricas `source: home-network`; los cambios FIRING y RESOLVED recorren el mismo flujo existente.

## Evaluación

Alertmanager ofrece `telegram_configs` y permite leer el token mediante `bot_token_file`, lo que evita codificarlo como valor en YAML. La configuración oficial de Alertmanager documenta este campo: [Alertmanager configuration](https://prometheus.io/docs/alerting/latest/configuration/). Esto no elimina la sensibilidad del secreto: el archivo debe vivir fuera de Git, tener permisos mínimos, montarse en solo lectura y no aparecer en logs, shell history, capturas o reportes.

Telegram sería una notificación externa y requiere salida de red y un bot de terceros. Antes de incorporarla, acordar destinatarios, privacidad, retención, mecanismo de rotación y prueba de recuperación. Mantener `send_resolved: true` para que el ciclo de incidente quede cerrado.

## Plan futuro, sujeto a revisión

1. Definir si las alertas domésticas realmente necesitan mensajería fuera del Incident Logger local.
2. Crear un secreto local mediante un canal seguro, sin compartirlo aquí ni ponerlo en `.env` o en Git.
3. Incorporar una plantilla/receiver que use solo `bot_token_file`, con el archivo montado en modo lectura desde una ruta local ignorada.
4. Limitar el receiver a la ruta `source=home-network`, conservando el receiver existente como destino de auditoría.
5. Validar `amtool check-config`, comprobar que el token no aparece en `docker compose config`, logs ni historial de Git y ejecutar una prueba autorizada de FIRING y RESOLVED.
6. Documentar destinatarios y rollback sin publicar identificadores del chat.

No se realiza ninguno de estos pasos en esta fase.
