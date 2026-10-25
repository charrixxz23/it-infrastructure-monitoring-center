# Home Network Monitoring

## Estado de esta entrega

La integración está activa en el entorno local de esta rama. La validación operativa actual obtuvo muestras recientes `1` para `probe_success` en router ICMP, ICMP externo, DNS A, DNS AAAA y HTTPS, y para `up{job="blackbox-exporter"}`. Esto confirma la conectividad de esas sondas desde la red de Docker Desktop/WSL2 en el momento de la comprobación; no demuestra disponibilidad continua ni describe Windows físico. El [informe redactado del incidente de preparación](incidents/blackbox-exporter-preparation.md) conserva la evidencia histórica y sus límites.

## Arquitectura

~~~mermaid
flowchart LR
  prometheus["Prometheus"]
  blackbox["Blackbox Exporter 0.28.0"]
  localfile["Targets locales ignorados por Git"]
  router["Router doméstico"]
  publicip["1.1.1.1"]
  dns["1.1.1.1:53 → example.com A/AAAA"]
  https["https://example.com"]
  grafana["Grafana: Home Network Monitoring"]
  alertmanager["Alertmanager existente"]
  incident["Incident Logger existente"]
  probes["home-probes: bridge con salida, sin puertos publicados"]
  localfile --> prometheus
  prometheus -->|"/probe, módulo fijo"| blackbox
  blackbox --> router
  blackbox --> publicip
  blackbox --> dns
  blackbox --> https
  grafana --> prometheus
  prometheus -->|"FIRING y RESOLVED"| alertmanager
  alertmanager --> incident
  prometheus -.->|miembro| probes
  blackbox -.->|miembro| probes
~~~

Prometheus conserva sus jobs existentes y añade un job de salud del exporter más sondas ICMP, DNS A/AAAA y HTTPS. El router se configura con un archivo de descubrimiento local, ignorado por Git. La plantilla versionada no contiene la dirección real. Los únicos destinos externos codificados son `1.1.1.1` y `example.com`; no hay descubrimiento de subredes.

`home-probes` es una red bridge de Compose dedicada con salida a los destinos de prueba. Solo Prometheus y Blackbox Exporter la comparten. Blackbox no publica puertos del host y no se une a las redes de Grafana/Alertmanager ni a la red interna de scraping. Esto reduce quién puede llamar al endpoint `/probe`; Docker bridge no es un firewall de aplicación y una intrusión en Prometheus podría solicitar sondas adicionales dentro de esa red.

La allowlist es una restricción de la configuración Prometheus versionada (targets,
módulos fijos y relabeling) y del validador CI; **Blackbox Exporter no aplica por sí
mismo una ACL de destinos al parámetro `target` de `/probe`**. El endpoint tampoco
tiene autenticación. La exposición queda acotada por la ausencia de puertos publicados
y la membresía de `home-probes`, cuyo único cliente previsto es Prometheus. Un
Prometheus comprometido o un servicio agregado a esa red podría pedir probes contra
otros destinos alcanzables; esto conserva un riesgo de egress/SSRF. No sumar miembros
ni destinos sin revisar expresamente esa frontera.

## Medidas y semántica

| Serie/indicador | Qué significa | Qué no significa |
|---|---|---|
| `up{job="blackbox-exporter"}` | Prometheus pudo obtener métricas del proceso exporter. | No confirma que el router o Internet respondan. |
| `up{job=~"home-.*"}` | Prometheus pudo llamar al endpoint de sonda y leer su respuesta. | No confirma éxito del destino. |
| `probe_success{job=...}` | Resultado del chequeo configurado (`1` éxito, `0` fallo) cuando existe muestra. | La ausencia de serie no se considera saludable. |
| `probe_duration_seconds` | Duración total de una sonda, en segundos. | No es latencia ICMP pura para sondas HTTP/DNS. |
| `probe_icmp_duration_seconds{phase="rtt"}` | Round-trip ICMP, en segundos; el dashboard la muestra solo cuando `probe_success=1`. | Un fallo no se presenta como cero milisegundos. |
| `probe_http_duration_seconds` | Fases del request HTTP, en segundos. | No mide ICMP. |
| `probe_dns_duration_seconds` | Fases de la consulta DNS, en segundos. | La consulta A/AAAA va al resolver explícito; no valida el DNS de Windows. |
| `probe_http_status_code` | Código de respuesta HTTP observado. | Un código ausente no se transforma en `200`. |

Los jobs usan intervalos de 30 segundos para ICMP y 60 segundos para DNS/HTTPS. La gráfica solo acumula historial después de activar la configuración. La evidencia entregada por el usuario (ping y resolución puntuales) es una prueba instantánea, no un historial de disponibilidad.

## Configuración y destinos

- Imagen fijada por versión y digest: `prom/blackbox-exporter:v0.28.0@sha256:e753ff9f3fc458d02cca5eddab5a77e1c175eee484a8925ac7d524f04366c2fc` (tag/manifest verificados en Docker Hub; el validador evita que cambie accidentalmente).
- Módulos fijos: ICMP IPv4; consulta DNS A y AAAA para `example.com`; GET HTTPS IPv4 para `https://example.com`.
- Las consultas DNS A/AAAA se envían directamente al destino permitido `1.1.1.1:53`, consultando `example.com`. No prueban el resolver de Windows/PowerShell.
- La sonda HTTPS usa el nombre `example.com`, por lo que su resolución de hostname depende del resolver de Docker Desktop. No sigue redirecciones a otros hosts; los códigos HTTPS 2xx y redirects esperados cuentan como endpoint accesible.
- El target del router debe ser una sola IP privada literal, bajo `probe_name: home-router`, en `config/prometheus/targets/home-network.yml`.
- No versionar el archivo local, `.env`, tokens, MAC, SSID, nombres de equipos ni capturas con etiquetas privadas. El repositorio ignora el archivo local y el validador comprueba su clasificación local.

## Permisos y límites de seguridad

Blackbox corre como UID/GID no privilegiado `65534:65534`, descarta todas las capabilities, usa root filesystem de solo lectura, `no-new-privileges`, almacenamiento temporal efímero y no recibe socket Docker/containerd. No se añadió `CAP_NET_RAW` ni `privileged: true`. Compose solicita `net.ipv4.ping_group_range` limitado a ese GID para intentar usar ping sockets no privilegiados. La compatibilidad de ese sysctl y de ICMP debe probarse en el kernel administrado por Docker Desktop/WSL2; esta configuración estática no demuestra que funcione. La documentación oficial explica que ICMP puede requerir un grupo permitido en `ping_group_range`, `CAP_NET_RAW` o privilegios mayores según el entorno. Si el modo sin capability falla, mantener ICMP desactivado o evaluar una alternativa explícita; no conceder privilegios automáticamente. [Configuración oficial Blackbox Exporter v0.28.0](https://github.com/prometheus/blackbox_exporter/blob/v0.28.0/CONFIGURATION.md), [imagen oficial](https://hub.docker.com/r/prom/blackbox-exporter).

El exporter tiene un endpoint de sondeo capaz de alcanzar el destino indicado por el cliente. No debe publicarse a host/LAN/Internet. Aquí solo escucha en su red Compose dedicada, pero la red no autentica pares: Prometheus es el único otro miembro previsto. Revisar cambios de Compose antes de sumar cualquier servicio a `home-probes`.

Este monitoreo solo existe mientras Windows, Docker Desktop/WSL2 y los contenedores requeridos permanezcan encendidos y conectados. Un corte eléctrico, reinicio, suspensión, cambio de red o actualización de Docker Desktop interrumpe las muestras. Docker/WSL2 mide desde su propia red; no equivale a una sonda desde Windows.

## Alertas e incidentes

Las reglas están en `config/prometheus/rules/home-network.yml` y se cargan por el glob existente. Esperan 1–3 minutos antes de notificar: exporter inaccesible, target faltante, error del endpoint de sonda, router ICMP no disponible, DNS del lado Docker o HTTPS externo fallido. DNS/HTTPS solo alertan cuando el router responde para separar un problema local de un problema de salida. Un fallo ICMP puede ser filtrado por el router y no prueba por sí solo que el router esté apagado.

Prometheus ya entrega alertas a Alertmanager; el receiver existente ya manda notificaciones FIRING y RESOLVED al Incident Logger. No se modificó Alertmanager ni el logger. Promtool tests nuevos simulan los estados y el ciclo de recuperación sin tráfico de red.

## Provisioning del dashboard

`Home Network Monitoring` es un archivo JSON independiente en `config/grafana/dashboards/`. El proveedor de archivos existente monta el directorio completo y vuelve a leerlo automáticamente. No se cambió el provider. El validador de dashboards verifica título, UID, consultas y 13 paneles. La disponibilidad del JSON/provisioning puede validarse estáticamente, pero la carga en Grafana requiere runtime activo.

La disponibilidad histórica usa `percentunit` con límites fijos `0` y `1` (eje de
0–100 % para las muestras 0/1 de `probe_success`). `Probe Endpoint Scrape Errors`
usa una consulta instantánea con comparación `== bool 0`: el valor cero significa que
hay series de targets y ninguna falla de scrape; `NO DATA` significa que no hay
series coincidentes. Los seis indicadores de estado usan alias breves; la ausencia
de una serie nunca se traduce a `UP`. Si faltan solo algunos targets, comprueba
Prometheus `/targets` y la alerta de target ausente: el contador solo agrega las
series existentes.

Ver pasos seguros de preparación, activación y reversión en el [runbook](runbooks/home-network.md). Para notificaciones externas, consultar el [plan de Telegram](home-network-notifications.md). La [plantilla de postmortem](templates/home-network-postmortem.md) distingue incidentes reales de ejercicios simulados.
