# Fase 5 — Diagramas Mermaid

Los diagramas separan el laboratorio actual de una propuesta sin desplegar. Las líneas punteadas o nodos marcados `proposed` no describen servicios que ya estén en línea.

## 1. Arquitectura actual local

~~~mermaid
flowchart LR
  subgraph Host["Windows + Docker Desktop / WSL2"]
    WinExporter["Python exporter\nmanual process"] --> DockerCLI["Docker CLI\nCompose project label"]
    DockerCLI --> DockerEngine["Docker Engine Linux"]
  end
  Node["Node Exporter\nLinux Engine metrics"] --> Prom["Prometheus\nscrape + rules + TSDB"]
  WinExporter -->|"host.docker.internal:9464"| Prom
  Prom -->|"datasource"| Grafana["Grafana\n14 + 12 provisioned panels"]
  Prom -->|"alerts"| AM["Alertmanager"]
  AM -->|"FIRING / RESOLVED webhook"| Logger["Incident Logger\nprivate Compose network"]
  Browser["Local browser"] --> Grafana
  Browser --> Prom
  Browser --> AM
~~~

Node Exporter metrics identify the Docker Engine Linux host, not Windows. No container is given a Docker or containerd socket. The host process still inherits Docker CLI permissions.

## 2. Proposed OCI ARM64 lab

~~~mermaid
flowchart TB
  subgraph OCI["OCI VCN · future, not deployed"]
    Public["Public HTTPS endpoint\n443 only"] --> Proxy["Reverse proxy\nTLS + access control"]
    Proxy -->|"private upstream"| Grafana["Grafana\nread-only presentation"]
    subgraph Private["Private monitoring network"]
      Prom["Prometheus\nprivate API"] --> Node["Node Exporter\nLinux host metrics"]
      Prom --> AM["Alertmanager\nprivate API"]
      AM --> Logger["Incident Logger\nprivate webhook"]
    end
    Grafana -->|"private datasource"| Prom
    Admin["Authorized admin CIDR\nor approved bastion"] -->|"SSH 22, restricted"| VM["ARM64 VM\nDocker Engine + Compose"]
  end
  WindowsExporter["Windows exporter\nnot portable"] -.->|"not used in OCI"| Prom
  ContainerExporter["Per-container Linux exporter\nsecurity review required"] -.->|"not enabled"| Prom
~~~

The load-balancer/public endpoint, hostname and certificate are still subject to tenancy eligibility and approval. Prometheus, Alertmanager, exporters and webhook must not be reachable from public ingress.

## 3. Separation of portal and lab

~~~mermaid
flowchart LR
  GitHub["Git repository\nsource + docs + screenshots"] -->|"authorized future Git integration"| Pages["Cloudflare Pages\nstatic Astro build"]
  Pages --> Visitor["Portfolio visitor\nHTTPS pages.dev"]
  GitHub -->|"separate future migration"| OCI["OCI ARM64 VM\nprivate monitoring lab"]
  OCI -.->|"no browser-to-Prometheus API"| Pages
  Visitor -->|"read static evidence only"| Pages
  RepoImages["Six checked-in screenshots"] -->|"copied byte-for-byte at build"| Pages
~~~

The static site does not fetch live metrics, use a data-source token, or depend on OCI availability. OCI can remain private without breaking the portfolio.

## 4. Proposed network and trust boundaries

~~~mermaid
flowchart LR
  Internet["Untrusted Internet"] -->|"TCP 443 only"| Edge["OCI public LB / HTTPS edge\nif eligible and approved"]
  Edge --> Proxy["Reverse proxy"]
  Proxy -->|"private HTTP(S) upstream"| Grafana["Grafana"]
  Admin["Authorized administrator CIDR"] -->|"TCP 22; key auth; restricted source"| SSH["SSH host access"]
  subgraph Private["Docker private network · no public port mappings"]
    Grafana --> Prom["Prometheus"]
    Prom --> Node["Node Exporter"]
    Prom --> AM["Alertmanager"]
    AM --> Logger["Incident Logger"]
  end
  Internet -.->|"denied"| Prom
  Internet -.->|"denied"| Node
  Internet -.->|"denied"| AM
  Internet -.->|"denied"| Logger
  Internet -.->|"denied"| DockerAPI["Docker Engine API"]
~~~

Egress is limited to reviewed necessities such as image registries, OS updates, DNS/NTP, certificate renewal and explicitly configured notification endpoints. No public Docker TCP API or UDP/TCP monitoring port is planned.

## 5. Future CI/CD with a deployment gate

~~~mermaid
flowchart TD
  Change["Pull request / push"] --> Existing["Existing CI\nPython + monitoring config"]
  Change --> Website["Website validation\nNode + pnpm lockfile\ncheck + build + asset tests"]
  Existing --> Result{"All validation passes?"}
  Website --> Result
  Result -->|"no"| Fix["Fix and rerun CI"]
  Fix --> Change
  Result -->|"yes"| Review["Human review and merge decision"]
  Review -->|"only after separate authorization"| Pages["Optional Cloudflare Pages Git integration"]
  Review -.->|"not part of this phase"| OCI["No automatic OCI deployment"]
  Secrets["No cloud credentials in CI"] -.-> Pages
~~~

The present repository workflow remains unchanged in this phase; the website job is documented as a later CI addition. There is no Pages project, deployment credential or OCI automation.
