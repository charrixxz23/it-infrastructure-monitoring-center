# Evidencia visual

Las seis imágenes originales de la galería y la captura adicional de Home Network Monitoring son PNG auténticas entregadas para esta documentación. Se revisaron visualmente y se verificaron sus dimensiones y chunks de metadata. Solo se normalizaron los nombres de las seis originales; sus hashes SHA-256 antes y después de renombrar coinciden. La captura adicional se conserva byte a byte, sin modificar sus datos.

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

## Otras evidencias

| Archivo | Dimensiones | Español | English |
|---|---:|---|---|
| [03-prometheus-targets.png](03-prometheus-targets.png) | 2559 × 1180 | Prometheus muestra 3/3 targets UP; incluye container-exporter además de los dos jobs del KPI de infraestructura. | Prometheus shows 3/3 targets UP, including container-exporter in addition to the two jobs counted by the infrastructure KPI. |
| [04-prometheus-alert-rules.png](04-prometheus-alert-rules.png) | 2559 × 1135 | Las cuatro reglas de infraestructura y TestIncident aparecen INACTIVE durante el estado normal de la captura. | The four infrastructure rules and TestIncident are INACTIVE while the captured system is in a normal state. |
| [05-alertmanager-no-active-groups.png](05-alertmanager-no-active-groups.png) | 2559 × 970 | No había grupos de alertas activos/recibidos en el momento de la captura. | No active/received alert groups were present at capture time. |
| [06-github-actions-ci.png](06-github-actions-ci.png) | 2550 × 1114 | La ejecución CI #4 del PR #1 muestra los cuatro jobs exitosos. | CI run #4 for PR #1 shows all four jobs succeeding. |

El estado 2/2 de Infrastructure Overview es intencional: ese KPI incluye Prometheus y Node Exporter. Prometheus Targets añade el job container-exporter y por eso presenta 3/3. Las reglas INACTIVE indican que no había condiciones de alerta activas; TestIncident se mantiene inactiva salvo durante una simulación controlada. Alertmanager sin grupos activos es coherente con la ausencia de alertas disparadas en esa captura.

## Revisión visual y privacidad

- No se observaron credenciales, contraseñas, tokens, rutas personales de Windows ni datos de incidentes.
- Las PNG no contienen chunks de texto de metadata.
- La captura Docker muestra nombres de servicios e IDs cortos efímeros de contenedor. No son credenciales, pero son identificadores operativos visibles en un repositorio público.
- La captura de GitHub Actions muestra el nombre público de la cuenta y del repositorio. El repositorio es público por decisión de su propietario.
- No se retocaron, recortaron ni reconstruyeron los valores o los píxeles de las imágenes.

Si en una actualización futura se reemplaza una captura, repite la revisión visual y de privacidad antes de enlazarla. No guardes en esta carpeta .env, reportes/exportaciones de data/, logs, archivos HAR ni tokens.
