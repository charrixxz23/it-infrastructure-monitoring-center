# Blackbox Exporter — informe de incidente de preparación

**Clasificación:** PREPARACIÓN / interrupción de la ruta de monitorización. No hay
evidencia suficiente para clasificarlo como una caída real del ISP.

## Resumen

El registro local de Incident Logger contiene un evento `BlackboxExporterDown` en
estado FIRING y su evento RESOLVED. Los `startsAt` y `endsAt` notificados muestran
una duración activa de **25 min 15 s**. El intervalo entre la recepción local de
ambos callbacks fue **25 min 0,114 s**. Son medidas distintas porque FIRING se
recibió 15,003 s después de `startsAt` y RESOLVED 0,117 s después de `endsAt`.

El evento confirma una pérdida temporal del scrape de Blackbox desde la perspectiva
de Prometheus; por sí solo no mide la disponibilidad del router ni del ISP. Durante
la brecha no hubo una serie `probe_success` disponible que permita concluir el estado
de los destinos. Por tanto, la causa de la interrupción de monitorización está
**parcialmente confirmada** y la disponibilidad de Internet durante toda la brecha
es **indeterminada**.

## Línea de tiempo (UTC)

| Hora | Evidencia redactada |
|---|---|
| 2026-10-25 01:27:45 | Una muestra histórica de `up{job="blackbox-exporter"}` vale `0`. |
| 2026-10-25 01:28:49.941 | `startsAt` del alerta `BlackboxExporterDown`. |
| 2026-10-25 01:29:04.944 | Incident Logger recibe FIRING localmente. |
| 2026-10-25 01:51:15 | La serie histórica del scrape de Blackbox vuelve a valer `1`. Es el tiempo de una muestra consultada, no una marca exacta del inicio de recuperación. |
| 2026-10-25 01:51:30 | Se observa por primera vez en el rango consultado el target de router y su resultado exitoso. |
| 2026-10-25 01:54:04.941 | `endsAt` del alerta. |
| 2026-10-25 01:54:05.058 | Incident Logger recibe RESOLVED localmente. |

La diferencia entre muestras de scrape `0` y `1` es de 23 min 30 s. El intervalo
activo del alerta es más largo porque las señales y notificaciones tienen distintos
tiempos de evaluación y entrega; no se interpreta la separación de muestras como la
duración exacta del fallo.

## Evidencia y límites

- El Incident Logger local conserva un FIRING y un RESOLVED para el mismo nombre de
  alerta. Este informe publica solo las marcas de tiempo y los estados necesarios;
  omite el log crudo, fingerprints, etiquetas de destino e identificadores.
- Las consultas históricas a Prometheus mostraron el scrape del exporter en `0` y
  luego en `1`; los cinco jobs de prueba comenzaron a presentar datos después de la
  recuperación observada.
- En la validación actual, Prometheus devuelve `1` para `up` del exporter, `up` de
  los cinco jobs domésticos y `probe_success` de las cinco sondas. La API de
  Alertmanager no muestra un `BlackboxExporterDown` activo. Los datos fueron
  consultados como instantáneas y son recientes; existe una diferencia de reloj
  local inferior a un segundo, por lo que no se atribuye precisión subsegundo a su
  antigüedad.
- Los seis servicios Compose observados están `running` y `healthy`, y la API de
  salud de Grafana respondió correctamente.
- La inspección posterior mostró tiempos de inicio de contenedores dentro del
  intervalo, pero los eventos Docker disponibles no conservaron el evento de ciclo
  de vida previo necesario para determinar por qué el scrape falló. Los tiempos de
  inicio no demuestran causalidad.

## Causa

**Confirmado:** Prometheus dejó de obtener un scrape válido del Blackbox Exporter
durante un intervalo y posteriormente recuperó el scrape. El alerta corresponde a
esa ruta de monitorización.

**No confirmado:** el desencadenante preciso (por ejemplo, reconciliación de
configuración, disponibilidad temporal del proceso/red o reinicio del runtime). La
evidencia local de eventos y logs disponible no alcanza para seleccionar una causa.

**No demostrado:** una caída del router o del ISP. El resultado de las sondas no
estuvo disponible durante parte de la brecha; no se afirma que la conexión externa
estuviera sana ni caída en ese período. Los resultados exitosos posteriores solo
describen las muestras posteriores.

## Seguimiento recomendado

1. Si vuelve a ocurrir, comparar `up{job="blackbox-exporter"}`, `up{job=~"home-.*"}`
   y `probe_success{job=~"home-.*"}` en el mismo rango y registrar las muestras sin
   etiquetas privadas.
2. Conservar localmente los eventos Docker antes de que expire su ventana; no subir
   logs crudos ni IDs al repositorio.
3. Corroborar cualquier supuesto incidente del ISP con una prueba independiente
   autorizada y fechada. No inferirlo de `BlackboxExporterDown`.
4. Mantener activa la distinción entre **PREPARACIÓN**, **INCIDENTE_REAL_DE_DESTINO**
   y **SIMULACIÓN** en los postmortems.

## Privacidad

Este informe omite IP domésticas, MAC, SSID, nombres de dispositivos, rutas locales,
fingerprints, etiquetas completas y contenido de logs. Los registros originales
permanecen solo en el entorno local.
