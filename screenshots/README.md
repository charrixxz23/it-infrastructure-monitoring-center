# Evidencia visual

No se incluyen capturas ficticias. Cuando el laboratorio se ejecute, guarda imágenes reales de:

1. Dashboard de Grafana con valores y ventana temporal visibles.
2. Prometheus Targets mostrando ambos jobs UP.
3. docker compose ps con healthchecks saludables.
4. Evidencia de la identidad Linux observada, por ejemplo node_uname_info.
5. Alertmanager con una alerta de prueba FIRING y luego RESOLVED, junto con el informe real generado por el simulador.
6. Historial JSONL filtrado a `TestIncident` para evidenciar los dos estados entregados por Alertmanager.

No fabriques capturas; usa solo una prueba ejecutada. Alertmanager está en `http://127.0.0.1:9093`; el logger no tiene una interfaz publicada y se consulta con `docker compose exec -T incident-logger python /app/incident_logger.py --tail 20`. Antes de compartir, oculta credenciales, nombres de usuario, rutas locales y cualquier dato sensible.
