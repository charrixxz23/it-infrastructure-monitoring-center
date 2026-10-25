# Plantilla de postmortem — Home Network Monitoring

> Antes de compartir el documento, elimina IP privadas, MAC, SSID, nombres de dispositivos, identificadores de chat, tokens, rutas personales y datos de incidentes.

## Clasificación del registro

- Tipo: **PREPARACIÓN** / **INCIDENTE_REAL_DE_DESTINO** / **SIMULACIÓN** (seleccionar uno; no mezclar evidencia real y simulada).
- PREPARACIÓN: una alerta de exporter, scrape, configuración o conectividad del laboratorio; no asumir una falla del ISP.
- INCIDENTE_REAL_DE_DESTINO: fallo observado en `probe_success` con muestra disponible y corroboración independiente.
- SIMULACIÓN: estado creado por pruebas automatizadas o ejercicio; no presentarlo como caída real.
- Fecha/hora y zona horaria:
- Autor/revisor:
- Alcance aprobado de las sondas:
- Fuente de evidencia: métricas reales / `promtool test rules` / ejercicio manual autorizado.
- ¿Hubo datos suficientes para determinar el estado del destino?: sí / no.

## Resumen

- Inicio y fin (UTC):
- Impacto observado:
- Duración:
- Señal que detectó el evento:
- Servicios/destinos afectados, usando alias genéricos:

## Línea de tiempo

| Hora UTC | Evento | Evidencia redactada | Real o simulada |
|---|---|---|---|
| | | | |

## Diagnóstico

- Estado de Blackbox Exporter:
- Estado de Prometheus scrape (`up`):
- Resultado de sonda (`probe_success`):
- Fase/latencia afectada:
- Router respondió en el mismo intervalo:
- ¿Se descartó un problema del exporter, DNS local o Docker Desktop?:
- Causa confirmada:
- Hipótesis no confirmadas:

## Respuesta y recuperación

- Acciones aprobadas ejecutadas:
- Evidencia de recuperación:
- Recepción FIRING:
- Recepción RESOLVED:
- Incidente registrado localmente:
- ¿Se reinició o alteró algún servicio? (describir autorización y motivo):

## Causa raíz y prevención

- Causa raíz confirmada o “desconocida”:
- Factores contribuyentes:
- Lo que funcionó:
- Lo que debe mejorar:
- Acciones, responsables y fecha objetivo:

| Acción | Responsable | Prioridad | Fecha | Estado |
|---|---|---|---|---|
| | | | | |

## Revisión de privacidad

- [ ] Sin dirección IP doméstica, MAC, SSID ni nombres de equipos.
- [ ] Sin credenciales, tokens, URL privadas o logs crudos.
- [ ] La evidencia simulada está claramente marcada y no se presenta como incidente real.
- [ ] Un intervalo sin datos se informa como indeterminado, no como UP ni como DOWN.
- [ ] El contenido fue revisado antes de cualquier publicación.
