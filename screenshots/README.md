# Evidencia visual

Las seis imágenes originales de la galería y la captura 07 de Home Network Monitoring son PNG auténticas. La evidencia 08 también parte de una captura auténtica: se recortó para incluir únicamente dos mensajes de la prueba sintética warning. El recorte preserva sin retoque los píxeles seleccionados de la imagen original; sus dos copias (aquí y en `website/public/screenshots/`) tienen el mismo SHA-256 y no contienen campos de metadata PNG.

## Dashboards de Grafana

| Archivo | Dimensiones | Español | English |
|---|---:|---|---|
| [01-infrastructure-overview.png](01-infrastructure-overview.png) | 2544 × 1393 | IT Infrastructure Overview: 14 paneles, KPI de targets 2/2 y volúmenes Windows C: / Docker virtual disk. | Infrastructure dashboard: 14 panels, 2/2 target KPI, Windows C: and Docker virtual disk. |
| [02-docker-container-monitoring.png](02-docker-container-monitoring.png) | 2532 × 1405 | Dashboard de contenedores: cinco contenedores observados, exporter UP y colección OK. | Container dashboard: five observed containers, exporter UP, and collection OK. |

Las dos imágenes principales se muestran en el [README](../README.md). Los valores corresponden al instante capturado, no a un estado garantizado en tiempo real.

## Home Network Monitoring

| Archivo | Dimensiones | Español | English |
|---|---:|---|---|
| [07-home-network-monitoring.png](07-home-network-monitoring.png) | 2559 × 1306 | Captura auténtica de Home Network Monitoring: cinco sondas y Blackbox Exporter saludable. Evidencia estática; no se publican targets privados ni métricas en vivo. | Authentic Home Network Monitoring screenshot: five probes and healthy Blackbox Exporter. Static evidence; private targets and live metrics are not published. |

La imagen se muestra en su propia sección del portal Astro mediante el visor accesible existente. El portal es estático y no consulta el entorno doméstico.

## Telegram — ciclo de alerta

| Archivo | Dimensiones | Español | English |
|---|---:|---|---|
| [08-telegram-alerting.png](08-telegram-alerting.png) | 555 × 235 | Recorte de una captura auténtica con FIRING y RESOLVED para una alerta doméstica sintética `warning`; horas visibles 08:19 y 08:24 del 4 de noviembre de 2026 (hora de Panamá). No representa una caída real de red. | Crop from an authentic screenshot showing FIRING and RESOLVED for a synthetic home-network `warning` alert; visible times are 08:19 and 08:24 on 4 November 2026 (Panama local time). It does not represent a real network outage. |

El recorte se hizo para excluir el encabezado de la cuenta, mensajes ajenos a la prueba y las notificaciones críticas posteriores cuya causa no fue correlacionada. Los valores de los dos mensajes visibles no se editaron. La captura se integra en el portal con el visor accesible existente.

## Otras evidencias

| Archivo | Dimensiones | Español | English |
|---|---:|---|---|
| [03-prometheus-targets.png](03-prometheus-targets.png) | 2559 × 1180 | Prometheus muestra 3/3 targets UP; incluye container-exporter además de los dos jobs del KPI de infraestructura. | Prometheus shows 3/3 targets UP, including container-exporter in addition to the two jobs counted by the infrastructure KPI. |
| [04-prometheus-alert-rules.png](04-prometheus-alert-rules.png) | 2559 × 1135 | Las cuatro reglas de infraestructura y TestIncident aparecen INACTIVE durante el estado normal de la captura. | The four infrastructure rules and TestIncident are INACTIVE while the captured system is in a normal state. |
| [05-alertmanager-no-active-groups.png](05-alertmanager-no-active-groups.png) | 2559 × 970 | No había grupos de alertas activos/recibidos en el momento de la captura. | No active/received alert groups were present at capture time. |
| [06-github-actions-ci.png](06-github-actions-ci.png) | 2550 × 1114 | La ejecución CI #4 del PR #1 muestra los cuatro jobs exitosos. | CI run #4 for PR #1 shows all four jobs succeeding. |

El estado 2/2 de Infrastructure Overview es intencional: ese KPI incluye Prometheus y Node Exporter. Prometheus Targets añade el job container-exporter y por eso presenta 3/3. Las reglas INACTIVE indican que no había condiciones de alerta activas; TestIncident se mantiene inactiva salvo durante una simulación controlada. Alertmanager sin grupos activos es coherente con la ausencia de alertas disparadas en esa captura.

## Revisión visual y privacidad

- No se observan credenciales, contraseñas, tokens, chat ID, rutas personales de Windows, direcciones domésticas, MAC ni SSID.
- La nueva PNG 08 no contiene campos de metadata; el crop coincide píxel por píxel con la región seleccionada de la fuente. La copia del sitio es idéntica a la de esta carpeta.
- La captura Docker muestra nombres de servicios e IDs cortos efímeros de contenedor. No son credenciales, pero son identificadores operativos visibles en un repositorio público.
- La captura de GitHub Actions muestra el nombre público de la cuenta y del repositorio. El repositorio es público por decisión de su propietario.
- Las seis capturas originales y la captura 07 no se alteraron. Solo la captura 08 está recortada; no se retocaron ni reconstruyeron los píxeles o valores que muestra.

Si en una actualización futura se reemplaza una captura, repite la revisión visual y de privacidad antes de enlazarla. No guardes en esta carpeta .env, reportes/exportaciones de data/, logs, archivos HAR ni tokens.
