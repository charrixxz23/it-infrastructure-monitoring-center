# Diagramas de arquitectura y operación

Los siguientes diagramas describen el estado del proyecto sin incluir servicios futuros. Mermaid está integrado en GitHub Markdown y se puede revisar directamente en la vista del repositorio.

## 1. Arquitectura

~~~mermaid
flowchart LR
    subgraph LocalHost["Equipo local: Windows + Docker Desktop / WSL2"]
        Browser["Navegador local"]
        Grafana["Grafana :3000"]
        Prometheus["Prometheus :9090"]
        Node["Node Exporter :9100"]
        AM["Alertmanager :9093"]
        Logger["Incident Logger :8080"]
        Exporter["Exporter Python en Windows<br/>127.0.0.1:9464"]
        DockerCLI["Docker CLI<br/>contexto desktop-linux"]
        Browser --> Grafana
        Browser --> Prometheus
        Browser --> AM
        Grafana --> Prometheus
        Prometheus --> Node
        Prometheus --> Exporter
        Exporter --> DockerCLI
        DockerCLI --> DockerEngine["Docker Engine Linux"]
        Prometheus --> AM
        AM --> Logger
    end
~~~

Node Exporter reporta el host Linux que ve el daemon. El proceso exporter reside en Windows y no se ejecuta en Compose. El proyecto contiene cinco servicios Compose; el exporter host-side es opcional y requiere inicio manual.

## 2. Flujo de métricas

~~~mermaid
flowchart LR
    Host["Linux del Docker Engine"]
    Node["Node Exporter"]
    Engine["Docker Engine"]
    CLI["Docker CLI<br/>filtro por etiqueta Compose"]
    Exporter["Exporter Python host-side"]
    Prom["Prometheus<br/>scrape y TSDB"]
    Infra["IT Infrastructure Overview"]
    Containers["Docker Container Monitoring"]

    Host --> Node -->|"scrape cada 15 s"| Prom
    Engine --> CLI --> Exporter -->|"host.docker.internal:9464"| Prom
    Prom --> Infra
    Prom --> Containers
~~~

El dashboard de infraestructura obtiene métricas de Node Exporter y del propio Prometheus. El dashboard Docker consume snapshots acotados a los contenedores etiquetados por este proyecto. Si no se inicia el exporter, las métricas de contenedor pueden estar ausentes; el dashboard no debe interpretarlo como estado saludable.

## 3. Flujo de incidentes

~~~mermaid
flowchart LR
    Metrics["Métricas scrapeadas"]
    Rules["Reglas Prometheus"]
    Pending["Condición pending<br/>duración configurada"]
    Firing["Alerta FIRING"]
    AM["Alertmanager<br/>agrupa y enruta"]
    Logger["Incident Logger<br/>webhook interno"]
    Store["Historial JSONL<br/>volumen local"]
    Recovery["Condición recuperada"]
    Resolved["Notificación RESOLVED"]

    Metrics --> Rules --> Pending --> Firing --> AM --> Logger --> Store
    Metrics --> Recovery --> Resolved --> AM
    Test["TestIncident<br/>inactiva por defecto"] -. simulador controlado .-> Rules
~~~

Prometheus evalúa las reglas; Alertmanager entrega notificaciones FIRING y RESOLVED al logger interno. TestIncident permanece inactiva por defecto y solo se usa en las pruebas controladas documentadas.

## 4. Flujo CI

~~~mermaid
flowchart TD
    Change["Push a rama configurada"]
    PR["Pull request hacia main"]
    Manual["workflow_dispatch"]
    CI["GitHub Actions: CI"]
    Python310["Python 3.10 tests"]
    Python313["Python 3.13 tests"]
    Static["Static quality and security checks"]
    Config["Dashboard and monitoring configuration validation"]
    Result{"¿Pasan los cuatro jobs?"}
    Review["Revisión del pull request"]
    Fail["Corregir el job fallido<br/>y volver a ejecutar CI"]

    Change --> CI
    PR --> CI
    Manual --> CI
    CI --> Python310
    CI --> Python313
    CI --> Static
    CI --> Config
    Python310 --> Result
    Python313 --> Result
    Static --> Result
    Config --> Result
    Result -->|"sí"| Review
    Result -->|"no"| Fail
    Fail --> CI
~~~

CI valida código y configuración en un runner Ubuntu, sin iniciar Compose ni acceder al Docker Desktop/WSL2 local. No despliega servicios ni ejecuta CD.
