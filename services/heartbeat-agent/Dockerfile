FROM python:3.13-alpine@sha256:7984cf56ceaddebe4fefeb75d64000b9f45d8b77b11a09b24524da6cdc3af0c8

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache \
      ca-certificates \
      dnsmasq=2.92_p2-r0 \
      iptables=1.8.13-r0 \
    && addgroup -S -g 10001 dnsproxy \
    && adduser -S -D -H -u 10001 -G dnsproxy dnsproxy

WORKDIR /app
COPY --chown=0:0 heartbeat_agent.py heartbeat_entrypoint.py provider_tls_probe.py /app/
ENTRYPOINT ["python", "/app/heartbeat_entrypoint.py"]
