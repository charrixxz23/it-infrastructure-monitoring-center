# Fase 5 — Arquitectura: portal y laboratorio cloud

**Estado:** diseño local, sin despliegue. **Referencia:** tag `v1.0.0` / commit `3e847f5`. **Revisado:** 2026-10-24.

## Decisión de arquitectura

El portal de portafolio y el laboratorio de métricas son sistemas independientes:

- `website/` es un sitio Astro estático, sin backend, base de datos, API de métricas, telemetría de visitas ni credenciales. La galería usa las seis capturas existentes, byte por byte.
- El laboratorio actual sigue ejecutándose en Windows + Docker Desktop/WSL2. Su Compose, Prometheus, Grafana, Alertmanager, Node Exporter y exporter Windows no se cambian en esta fase.
- La futura VM propuesta es Linux ARM64 en OCI. Sus servicios de observabilidad solo tendrían conectividad privada. Un reverse proxy sería la única ruta web pública, condicionada a TLS y una revisión de publicación.
- Cloudflare Pages serviría los archivos estáticos. No consultaría la VM ni intentaría presentar métricas vivas. El portal seguirá funcionando aunque OCI no esté disponible.

Los diagramas, con el estado real y la propuesta diferenciados, están en [diagrams.md](diagrams.md).

## Arquitectura local verificada en el repositorio

Cinco servicios Docker Compose: Prometheus, Grafana, Node Exporter, Alertmanager e Incident Logger. Prometheus scrapea su propio endpoint, Node Exporter y, si se inicia manualmente, `container-exporter` en Windows. Grafana consume Prometheus; Alertmanager entrega notificaciones FIRING/RESOLVED al Incident Logger. Los dashboards se cargan mediante aprovisionamiento desde archivos.

Node Exporter observa el Linux visible al daemon Docker Desktop/WSL2. No equivale a métricas de Windows. La ruta `host.docker.internal:9464` es específica del camino de red de Docker Desktop y no se presupone en OCI. El proceso Windows usa Docker CLI y hereda derechos importantes sobre el daemon; que solo solicite estadísticas no convierte ese acceso en una frontera de solo lectura.

No se montan sockets Docker/containerd dentro de los contenedores. cAdvisor no está habilitado. Los detalles del exporter actual están en [docs/container-exporter.md](../container-exporter.md) y la evaluación de cAdvisor en [docs/container-monitoring-assessment.md](../container-monitoring-assessment.md).

## Arquitectura cloud propuesta

Una única VM `VM.Standard.A1.Flex` Linux ARM64 para un entorno demostrativo de cinco servicios, sujeto a comprobar capacidad, elegibilidad, imágenes y rendimiento. El dimensionamiento inicial propuesto es 2 OCPU y 8 GiB RAM, no una medición ni garantía de que el servidor sea asignado. Se presupuestan 50 GiB para boot volume y, si hace falta, un volumen de datos de hasta 50 GiB; el total siempre se contrasta con el límite de 200 GB.

La VM usaría un archivo Compose cloud separado y versionado solo después de aprobar la migración. No se copiarían sin cambios:

- Los bind mounts y supuestos de `/proc`, `/sys`, `/` de Docker Desktop.
- El target `host.docker.internal` del exporter Windows.
- La publicación local de puertos en loopback de la configuración de desarrollo.
- La retención de datos y configuración de red sin verificar almacenamiento, límites y superficie expuesta.

Red propuesta: VCN y subred con entrada predeterminada denegada; una capa de entrada HTTPS conecta con el reverse proxy; el reverse proxy solo puede alcanzar Grafana; Prometheus, Node Exporter, Alertmanager e Incident Logger permanecen en la red privada de monitoreo. SSH de administración se limita al CIDR autorizado del administrador o a un servicio de acceso administrativo aprobado. Prometheus y Alertmanager no se publican en Internet.

La arquitectura pública debe tener un hostname válido y terminación TLS antes de anunciar una demo. Si no hay un dominio existente autorizado, el dashboard vivo permanece privado y el portafolio solo presenta capturas. No se compra un dominio ni se modifica DNS para esta fase.

## Exporter por contenedor: pendiente de aprobación de seguridad

El exporter Windows no es portable a Linux: depende de un Docker CLI local y del contexto Docker Desktop. Un proceso Linux con acceso al socket o al grupo `docker` también obtendría privilegios equivalentes al control del daemon. No se despliega cAdvisor ni se monta `docker.sock` en ningún contenedor.

La línea base cloud mantiene métricas del host con Node Exporter, sin atribución por contenedor. Para reabrir métricas individuales, comparar un colector de aplicaciones, acceso de host a cgroups v2, una API/proxy con lista estricta y un proceso externo de Docker CLI. El diseño debe demostrar qué rutas y métodos permite, permisos de lectura efectivos, identidad de contenedor, cardinalidad y tratamiento de fallos. Un bind mount `:ro` del socket no demuestra que la API Docker sea de solo lectura. Hasta entonces el exporter Linux queda fuera de la arquitectura aprobada.

## Recursos, datos y disponibilidad

- Presupuesto de muestra: Prometheus 2 GiB máximo; Grafana 1 GiB; Alertmanager 512 MiB; Node Exporter 256 MiB; Incident Logger 256 MiB; reverse proxy 256 MiB. Reservar al menos 2 GiB para SO/daemon y mantener margen sin asignar; volver a medir en ARM64.
- Prometheus inicia con retención corta (propuesta 7 días y límite TSDB de 2 GiB) y un disco de datos separado. Ajustar solo con medición real, espacio y volumen de ingesta observados.
- Rotación de logs acotada, healthchecks locales y alertas de espacio de disco antes de abrir tráfico. Los volúmenes persistentes y backups tienen cuota; el backup no sustituye restaurar y probar.
- Una instancia Always Free puede reclamarse bajo las condiciones publicadas por Oracle. No es un servicio con SLA de producción; preservar configuraciones y tratar el historial de métricas como recreable.

Las cuotas, consumo estimado y riesgo de cobro están en [cost-assessment.md](cost-assessment.md) y [oracle-arm64-deployment-plan.md](oracle-arm64-deployment-plan.md). Las reglas de red y publicación, en [security-model.md](security-model.md).

## Estado de entregables

| Elemento | Estado |
|---|---|
| Portal estático Astro y capturas locales | Implementado; typecheck, build, assets, documentación y revisión responsive local validados |
| Cloudflare Pages | Preparado en documentos; sin cuenta, proyecto, token ni despliegue |
| OCI Always Free ARM64 | Estudio de capacidad y arquitectura; sin cuenta ni recursos provisionados |
| Compose cloud separado | Requisito para una fase posterior; no creado aquí |
| Exporter Linux por contenedor | Bloqueado hasta revisión de permisos y aislamiento |
| Dashboard Grafana públicamente compartido | No publicado; acceso público pendiente de decisión y datos sintéticos/sanitizados |

`promtool` y Gitleaks no estaban instalados en el equipo durante esta revisión. Sus validaciones específicas quedan pendientes; los validadores locales de dashboards, documentación, JavaScript y repo hygiene sí pasaron. La auditoría de dependencias del sitio no reportó vulnerabilidades conocidas.

El workflow incorpora un job web fijado a Node 24.22.1 y pnpm 11.25.0. La ejecución remota para esta rama queda pendiente de un push/PR autorizado; no se publica desde esta fase.

## Referencias primarias

- [Oracle Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Prometheus security model](https://prometheus.io/docs/operating/security/)
- [Docker Engine security](https://docs.docker.com/engine/security/)
- [Grafana security configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-security/)
