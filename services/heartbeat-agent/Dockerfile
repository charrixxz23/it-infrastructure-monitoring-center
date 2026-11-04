FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache ca-certificates

WORKDIR /app
COPY --chown=65534:65534 heartbeat_agent.py /app/heartbeat_agent.py
USER 65534:65534

ENTRYPOINT ["python", "/app/heartbeat_agent.py"]
