# Fase 5 — Modelo de seguridad y exposición

**Estado:** modelo de diseño; no se han creado recursos ni cambiado controles remotos. Este documento describe la frontera de confianza que debe preservarse en fases futuras.

## Separación de superficies

| Superficie | Exposición planeada | Datos | Control requerido |
|---|---|---|---|
| Portal Astro / Pages | Sitio estático público, sujeto a revisión y autorización | README, diagramas, documentación aprobada y seis capturas históricas | No incluir credenciales, rutas personales, incidentes privados ni métricas vivas; tratar previews como públicas |
| VM OCI | Ninguna hasta aprobar red, costo, hostname y TLS | Laboratorio de métricas y registros | Entrada denegada por defecto; administración SSH restringida a origen autorizado |
| Grafana | Solo a través de una capa web revisada; inicialmente privada | Dashboards y consultas de Prometheus | Autenticación, rol mínimo, datasource privado y revisión del contenido antes de compartir |
| Prometheus / Alertmanager / exporters / webhook | Red privada del stack | Métricas, reglas y eventos | No publicar endpoints directamente a Internet |
| Docker Engine | Acceso de administración local restringido | Control del host y contenedores | No exponer API TCP pública ni montar sockets en exporters |

El portal es una presentación estática y no tiene conectividad hacia OCI. El plan base mantiene dashboards vivos privados; capturas sanitizadas son la evidencia pública. Una URL difícil de adivinar no es control de acceso.

## Matriz de puertos propuesta para OCI

La tabla es una propuesta de reglas explícitas para una futura VM, no una configuración aplicada. “Privado” significa solo rutas de red internas autorizadas. No se trasladan los mapeos de loopback del Compose local a una interfaz pública de VM.

| Origen | Destino | Puerto/protocolo | Exposición | Propósito / decisión |
|---|---|---:|---|---|
| Internet | Balanceador/reverse proxy | TCP 443 | Público, condicional a revisión TLS | Única entrada web prevista; termina TLS y reenvía a Grafana |
| CIDR administrativo aprobado | VM vía bastion o endpoint de gestión | TCP 22 | Restringido; deshabilitado si no se requiere acceso directo | SSH con clave y controles de host; nunca abierto a `0.0.0.0/0` |
| Reverse proxy | Grafana | TCP 3000 | Privado | UI autenticada; no publicar el puerto del contenedor |
| Grafana | Prometheus | TCP 9090 | Privado | Datasource y consultas internas |
| Prometheus | Node Exporter | TCP 9100 | Privado | Scrape de métricas de host |
| Prometheus | Alertmanager | TCP 9093 | Privado | Entrega de alertas |
| Alertmanager | Incident Logger | TCP 8080 | Privado | Webhook interno `/webhook` |
| Prometheus | Prometheus | TCP 9090 | Loopback/red interna | Scrape local del propio servidor |
| Cualquier origen público | Prometheus, Alertmanager, exporters, webhook y Docker API | Cualquier puerto administrativo/de métricas | Denegado | No existe excepción de ingreso directo |

Puerto `9464` es una ruta actual de Docker Desktop hacia el exporter Windows y no se habilita en el diseño OCI. No se expone Docker Engine API en TCP. Cualquier nuevo flujo requiere actualizar la matriz y justificar fuente, destino, protocolo y necesidad antes de abrirlo.

## Identidad y secretos

- No guardar `.env`, contraseñas, tokens, credenciales cloud, claves SSH o certificados privados en el sitio ni en documentación versionada.
- No añadir secretos al build de Pages ni a los workflows existentes. La publicación futura no requiere secretos de OCI.
- Mantener acceso administrativo a la cuenta cloud, al equipo y a GitHub con MFA y privilegio mínimo. Revisar las autorizaciones OAuth que solicite una integración Git antes de concederlas.
- Las reglas de firewall del host y de OCI deben limitar SSH al CIDR administrativo aprobado. No abrir Grafana, Prometheus `9090`, Alertmanager `9093`, Node Exporter `9100`, el exporter `9464` ni el webhook al público.

## Docker y exporter de contenedores

El proceso Python opcional en Windows invoca Docker CLI. Acceder al daemon de Docker implica capacidad significativa; filtrar solo el proyecto Compose no reduce los privilegios de la identidad del proceso. El hecho de solicitar `docker stats` no constituye una API de solo lectura.

La fase cloud no traslada ese exporter. No montar `docker.sock` o sockets containerd, usar `privileged: true`, conceder el grupo `docker` a un servicio web ni publicar la API TCP del daemon. Un montaje de socket como solo lectura restringe el sistema de archivos, pero por sí solo no autoriza métodos API exclusivamente de lectura. Las métricas por contenedor quedan fuera hasta una revisión independiente de permisos, rutas y fallos.

## Métricas, Grafana y capturas

Prometheus y exporters exponen información operativa y no deben quedar directamente accesibles desde Internet. Una capa pública futura solo debería terminar TLS y reenviar rutas aprobadas hacia Grafana; datasource y APIs continúan privadas.

La función de dashboard compartido de Grafana es de solo lectura, pero una vista compartida puede ser pública. Además, el rol Viewer puede consultar los data sources disponibles. No compartir el Grafana vivo sin revisar identidad, permisos de data source, variables y consultas. La web de esta fase muestra PNG estáticos y enlaces a GitHub; no incorpora la sesión de Grafana ni credenciales.

| Modo | Datos / audiencia | Evaluación para esta fase |
|---|---|---|
| Viewer autenticado | Usuario inicia sesión; puede consultar data sources a los que tiene acceso | Opción apropiada para un demo vivo solo si rol, datasource, variables y consultas se revisan; mantenerlo privado inicialmente |
| Shared dashboard | Presentación externa de solo lectura y potencialmente pública por URL | Reduce la superficie de edición, pero no hace privados los paneles ni elimina carga de consultas; solo publicar datos sanitizados |
| Snapshot | Copia estática de la visualización, no conectada a métricas vivas | Puede vivir en infraestructura de snapshot y convertirse en copia pública; no usar si contiene labels, eventos o identificadores sensibles |
| Proxy público hacia Grafana | UI servida tras reverse proxy y TLS | Requiere autenticación adicional, control de rutas, rate limiting y prueba de que APIs administrativas quedan cerradas; no habilitar anónimo |

La alternativa aprobada para el portal de esta fase son capturas revisadas y fechadas, no un enlace de Grafana público.

## Headers del sitio estático

`website/public/_headers` establece CSP de misma procedencia, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, política de referer y permisos de navegador restringidos. HSTS se aplica al host HTTPS después de publicar en Pages; antes de usar un dominio propio, verificar que todos los subdominios cubiertos admitan HTTPS. Ningún header reemplaza autenticación ni revisión del contenido.

## Fuentes oficiales

- [Docker Engine: seguridad](https://docs.docker.com/engine/security/)
- [Docker: el grupo `docker` concede privilegios root](https://docs.docker.com/engine/install/linux-postinstall/)
- [Prometheus: modelo de seguridad](https://prometheus.io/docs/operating/security/)
- [Grafana 13.2: configuración de seguridad](https://grafana.com/docs/grafana/v13.2/setup-grafana/configure-security/)
- [Grafana 13.2: dashboards compartidos](https://grafana.com/docs/grafana/v13.2/visualizations/dashboards/share-dashboards-panels/shared-dashboards/)
- [Cloudflare Pages: headers personalizados](https://developers.cloudflare.com/pages/configuration/headers/)
