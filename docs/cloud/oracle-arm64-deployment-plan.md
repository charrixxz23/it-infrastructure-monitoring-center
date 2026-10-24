# Plan de migración a Oracle Cloud Always Free ARM64

**Estado:** propuesta técnica; no desplegada ni conectada a una cuenta. **Verificado:** 2026-10-24 con documentación Oracle y manifiestos OCI públicos. Revalidar todo antes de provisionar.

## 1. Alcance y condiciones de decisión

La referencia es una VM `VM.Standard.A1.Flex` de 2 OCPU y 8 GiB RAM, una sola instancia, con sistema Linux ARM64, Docker Engine y Compose. El objetivo es un laboratorio educativo pequeño, no un servicio productivo ni de alta disponibilidad. La instalación actual de Windows/WSL2 permanece intacta.

Oracle publica para Always Free 1.500 OCPU-horas y 9.000 GB-horas mensuales de A1; el máximo indicado para la cuenta Always Free equivale a 2 OCPU y 12 GB. Para 2 OCPU/8 GiB durante un mes de 31 días, el uso estimado es 1.488 OCPU-horas y 5.952 GB-horas. La instancia cabe en esas dos cuotas si está elegible, disponible y no se suma otra instancia A1. Esto no garantiza capacidad ni creación: el cómputo gratuito debe ubicarse en la home region y Oracle documenta errores temporales “out of host capacity”. Una instancia Always Free también puede ser reclamada si, durante 7 días, se cumplen los umbrales de CPU p95 <20 %, red <20 % y memoria <20 % en A1.

No se usaron los créditos de prueba de US$300, no se solicitó una cuenta y no se recomienda pasar a Pay As You Go para completar la fase. La elegibilidad personal, región, límites restantes, capacidad real, precio efectivo y opciones de facturación no se pueden verificar sin la consola de OCI; no se presupone ninguno de ellos.

## 2. Matriz de recursos

La matriz completa y los controles de gasto están en [cost-assessment.md](cost-assessment.md). Límites de Oracle verificados en la fecha de este documento:

| Recurso propuesto | Cuota Always Free publicada | Selección de diseño | Riesgo / control |
|---|---|---|---|
| A1 Compute | 1.500 OCPU-h / 9.000 GB-h al mes; máximo equivalente 2 OCPU y 12 GB para tenancy Always Free | 1 VM ARM64, 2 OCPU/8 GiB; estimado 1.488 OCPU-h y 5.952 GB-h en 31 días | Capacidad de home region no garantizada; verificar etiqueta Always Free antes de crear. Riesgo de recuperación por inactividad |
| Block Volume | 200 GB combinados entre boot y block volumes, en home region | Boot de 50 GB + hasta 50 GB de datos | Dejar margen, revisar siempre el total combinado y no crear volúmenes en otra región |
| Backups de volumen | Cinco backups combinados de boot/block en home region | Mantener 1 backup retenido durante la prueba, sujeto a verificar cuota disponible | No reemplaza export de configuración ni prueba de restauración |
| Flexible Load Balancer | Uno de 10 Mbps para tenancies elegibles creadas desde 2020-12-15 | Candidato para publicar exclusivamente HTTPS y mantener la VM sin IP pública directa | Verificar elegibilidad y costo en Console antes de aprovisionarlo; no exceder cuota |
| Salida de datos | 10 TB/mes outbound Always Free | Sin promesa de volumen; medir tráfico y limitar paneles/visitas | Excedente, servicios relacionados o cuenta no elegible podrían generar cargos |
| Object Storage | Hasta 20 GB Always Free | No necesario en la primera implementación | La cuota varía por estado de cuenta; no usar prueba/recursos de pago |
| DNS / dominio propio | No se presupone un dominio gratuito incluido | Ninguno hasta aprobación | No comprar dominio ni cambiar DNS; sin hostname y TLS no se anuncia Grafana público |

Oracle también publica que el balanceador flexible Always Free tiene mínimo y máximo de 10 Mbps. La ruta de entrada puede seguir bloqueada si la instancia, red o TLS no tienen elegibilidad comprobada. La alternativa es dejar Grafana privado y mantener solo el portal estático público.

## 3. Compatibilidad de imágenes ARM64

Los manifiestos OCI/Docker Hub de los tags del repositorio se consultaron en modo lectura desde el registro público el 2026-10-24. Los digest siguientes corresponden al descriptor `linux/arm64` del tag consultado, no sustituyen pinning de digest en una futura configuración.

| Referencia actual | `linux/arm64` | Digest de plataforma observado | Lectura técnica |
|---|---:|---|---|
| `prom/prometheus:v3.15.0` | Sí | `sha256:6b41f7a45cfbd1d259a78701ee5e14fc2ad9383c9aa5d0427345a18539bc3c91` | Imagen oficial multi-arquitectura; probar TSDB, reglas y persistencia sobre ARM64 nativo |
| `prom/node-exporter:v1.12.1` | Sí | `sha256:c9ef89f9464f09e7234decaae68a80ab856ff0014435677a99fd48b03dd410ea` | Imagen ARM64 presente; el acceso a host `/proc`, `/sys` y filesystem requiere una configuración Linux revisada |
| `prom/alertmanager:v0.34.1` | Sí | `sha256:47a1dc7e74f1e755e29f74d392262f8d1da41f2ada5653911199bf07219e41d9` | Imagen ARM64 presente; verificar agrupación, webhook y storage en instancia nativa |
| `grafana/grafana:13.2.4` | Sí | `sha256:15254b507dd6da013d57af4fc7e902721110b70d37282ab9ae866290803bd752` | Imagen ARM64 presente; revisar provisioning, plugin y renderizado en ARM64 |
| `python:3.13-alpine` (base del Incident Logger) | Sí | `sha256:82dd83b5bb181e36f389c6526af7012943022888047c74bf4cba362bfa07a83c` | Tag `3.13-alpine` puede cambiar; el logger usa biblioteca estándar, pero la base debe fijarse por digest antes de migrar |

La verificación confirma que el registro ofrece una variante ARM64, no que todo el stack haya pasado pruebas en A1. Los tags de Compose hoy no fijan digest. La futura configuración debe fijar versiones y, después de revisión, digest multi-arch; confirmar healthchecks (`wget`), almacenamiento, permisos, `cgroups v2`, red y comportamiento del filesystem en Ubuntu u Oracle Linux ARM64. No usar emulación x86 como aprobación silenciosa.

## 4. Delta necesario respecto a Docker Desktop/WSL2

| Elemento actual | Acción propuesta antes de migrar |
|---|---|
| `host.docker.internal:9464` | Eliminarlo de la configuración cloud; el exporter actual vive en Windows y no existe en OCI |
| Bind mounts `/proc`, `/sys`, `/` de Node Exporter | Diseñar mounts específicos de Linux, solo lectura, probar los collectors y limitar qué host paths ve el contenedor |
| Puertos publicados a `127.0.0.1` | En la VM no publicar puertos del stack; el reverse proxy accede por red privada de Docker |
| Red `local-access` de Docker Desktop | No trasladar. Crear redes cloud privadas separadas para observabilidad e ingreso del proxy |
| Volúmenes y retención local | Limitar Prometheus a una retención inicial de 7 días / 2 GiB y reservar espacio; probar restauración |
| `TestIncident` y datos de incidentes | Mantener prueba inactiva por defecto; usar simulación controlada, sin datos privados en evidencias públicas |
| cAdvisor y sockets | No incorporar cAdvisor, `docker.sock`, containerd sockets ni `privileged: true` |

No editar ni copiar `compose.yaml` en esta fase. Si la migración obtiene aprobación, crear un manifiesto separado `compose.cloud.yaml` (o una carpeta `deploy/oracle/`) y sus validadores; la configuración local continúa siendo la referencia de v1.0.0.

## 5. Exporter de métricas por contenedor

El exporter existente usa Docker CLI desde Windows. No se porta automáticamente a Linux ni se coloca en un contenedor. Docker documenta que el grupo `docker` concede privilegios de root; el socket local tampoco se vuelve de solo lectura por montarlo con `:ro`. Una API Docker remota abierta sin TLS/autenticación concede control del daemon.

La base cloud omite métricas por contenedor. Antes de reabrir ese alcance:

1. Preferir instrumentar cada aplicación cuando la métrica pueda exponerse sin consultar el daemon.
2. Si se requiere CPU/memoria por cgroup, evaluar lectura acotada de cgroups v2 y el mapeo de identidades sin acceso de control de Docker.
3. Si se requiere Docker API, demostrar una lista de rutas/métodos permitidos y que no habilite crear procesos, contenedores, mounts, exec, imágenes o volúmenes.
4. Ejecutar el colector separado, sin privilegios de contenedor, sin rutas de escritura y sin endpoint público; documentar que cualquier acceso al daemon es una capacidad crítica.

La matriz de decisión está en [security-model.md](security-model.md). La aprobación sigue pendiente y no se considera una condición para iniciar un laboratorio de métricas de host.

### Comparación de alternativas

| Alternativa | Ventaja | Límite/riesgo | Decisión de diseño |
|---|---|---|---|
| Instrumentación de aplicaciones | Métricas de negocio/aplicación con acceso explícito y sin consultar el daemon | No aporta CPU/memoria genérica de todos los contenedores | Preferir cuando las aplicaciones bajo control puedan instrumentarse |
| Node Exporter / cgroups v2 de solo lectura | Linux nativo; puede observar recursos del host y datos cgroup acotados | La identidad/mapeo de contenedores y permisos de cgroup dependen de kernel, runtime y montaje | Evaluar en una VM ARM64 real; limitar paths/lecturas y comprobar que no hay rutas de control |
| cAdvisor | Exporter conocido con series por contenedor | Requiere acceso a cgroups y a partes del host; determinados modos/mounts pueden ampliar la superficie de ataque | No habilitar por defecto; piloto aislado solo tras revisión concreta de mounts y permisos, sin `privileged` ni sockets de control |
| Exporter custom por Docker API | Puede filtrar proyecto/labels y limitar la serie expuesta | Leer API del daemon confiere una capacidad crítica; filtros de aplicación no reducen privilegios del cliente | Excluido de la baseline hasta demostrar API proxy allowlist por rutas/métodos y ausencia de acciones de control |
| Exporter Docker CLI host-side | Reutiliza el collector Python local existente | Necesita CLI/contexto y acceso daemon en el host; la solución Windows depende de Docker Desktop | No migrar automáticamente ni ejecutarlo dentro de un contenedor |

Ninguna alternativa es “segura” solo por ejecutar métricas. Requerir prueba de permisos efectivos y del impacto de una vulnerabilidad del proceso antes de incorporar series por contenedor.

## 6. Dimensionamiento preliminar

Para una VM de 8 GiB, estos son límites iniciales de planificación, no consumos medidos:

| Proceso | Límite inicial propuesto | Notas |
|---|---:|---|
| Prometheus | 2 GiB RAM; 0.8 CPU | 7 días / 2 GiB de datos; observar cardinalidad y WAL |
| Grafana | 1 GiB RAM; 0.4 CPU | Sin plugins no necesarios; no habilitar uso anónimo |
| Node Exporter | 256 MiB; 0.2 CPU | Métricas solo del host Linux y scrape privado |
| Alertmanager | 512 MiB; 0.2 CPU | Retención limitada y storage persistente |
| Incident Logger | 256 MiB; 0.1 CPU | Python stdlib, filesystem persistente con rotación |
| Reverse proxy | 256 MiB; 0.2 CPU | TLS y proxy a Grafana, sin API de monitoreo |
| Servicios sintéticos | Hasta 512 MiB total; 0.3 CPU | No incluidos en la línea base; agregar solo con prueba/limitación explícita |
| SO + Docker Engine + margen | Al menos 2 GiB RAM sin asignar | Reservar memoria para kernel, caches, actualizaciones y picos |

Las cotas suman aproximadamente 4.8 GiB de procesos y dejan más de 3 GiB para SO y margen. Medir carga real y ajustar antes de aceptar; no mantener una instancia con carga artificial para intentar evitar reclamación por inactividad.

## 7. Orden futuro de implementación (requiere aprobación separada)

1. Revalidar cuotas, home region, disponibilidad A1, límites y etiquetas Always Free sin seleccionar recursos de pago.
2. Revisar OS ARM64 y digests exactos. Probar en host nativo ARM64; un manifest presente no sustituye el smoke test.
3. Crear Compose cloud separado, límites CPU/memoria, redes privadas, volúmenes acotados y logs rotados. Nada de socket Docker, `privileged`, credenciales en Git ni puertos directos de Prometheus/exporters.
4. Validar Node Exporter contra el host real, luego Prometheus scrape/rules, Alertmanager webhook, logger, Grafana datasource/provisioning y restauración.
5. Publicar solo reverse proxy HTTPS, después de decisión de hostname/certificado, revisión de datos y autorización explícita. Hasta entonces Grafana sigue privado y el portal usa capturas.

## Fuentes oficiales

- [Oracle Always Free resources (home region, cuotas, capacidad, reclamación)](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Oracle Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [Docker Engine security](https://docs.docker.com/engine/security/)
- [Docker post-install: acceso del grupo `docker`](https://docs.docker.com/engine/install/linux-postinstall/)
- [Prometheus security model](https://prometheus.io/docs/operating/security/)
- [Docker Hub: Prometheus](https://hub.docker.com/r/prom/prometheus/tags?name=v3.15.0), [Node Exporter](https://hub.docker.com/r/prom/node-exporter/tags?name=v1.12.1), [Alertmanager](https://hub.docker.com/r/prom/alertmanager/tags?name=v0.34.1), [Grafana](https://hub.docker.com/r/grafana/grafana/tags?name=13.2.4), [Python](https://hub.docker.com/_/python?tab=tags&name=3.13-alpine)
