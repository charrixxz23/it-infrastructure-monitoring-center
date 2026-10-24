FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INCIDENT_LOG_PATH=/data/incidents.jsonl \
    INCIDENT_LOG_MAX_BYTES=5242880 \
    INCIDENT_LOG_BACKUP_COUNT=3

WORKDIR /app
COPY incident_logger.py /app/incident_logger.py
RUN mkdir -p /data && chown 65534:65534 /data
USER 65534:65534

EXPOSE 8080
ENTRYPOINT ["python", "/app/incident_logger.py"]
