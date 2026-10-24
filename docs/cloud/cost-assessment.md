# Fase 5 — Costos, cuotas y controles

**Fecha de verificación documental:** 2026-10-24. **No hay cuenta OCI/Cloudflare conectada ni recursos facturados.** Esta es una evaluación de cuotas públicas; no es una cotización ni una lectura de consola.

## OCI Always Free

| Recurso | Límite gratuito publicado | Uso de diseño | Costo previsto dentro de cuota | Riesgo y control preventivo |
|---|---|---|---|---|
| Ampere A1 Flex | 1.500 OCPU-h/mes y 9.000 GB-h/mes; máximo de tenancy Always Free 2 OCPU/12 GB | 1 VM, 2 OCPU/8 GiB; 1.488 OCPU-h y 5.952 GB-h en 31 días | US$0 mientras se cumplan los límites Always Free y la cuenta sea elegible | No hay garantía de capacidad en home region. Revisar etiqueta Always Free antes de crear. No añadir instancias A1 ni usar shapes de pago |
| Boot + datos block | 200 GB combinados; boot y block cuentan juntos, en home region | 50 GB boot + hasta 50 GB de datos; total objetivo ≤100 GB | US$0 dentro de los 200 GB elegibles | No crear volúmenes fuera de home region. Revisar tamaño/backup y borrar costos solo con autorización; no depender de volúmenes temporales |
| Backups block | Cinco backups combinados | Uno en el diseño mínimo | US$0 si la cuota gratuita sigue disponible | Los cinco son cuota de cantidad, no una garantía de restauración. Probar export y recuperación |
| Flexible Load Balancer | Uno de 10 Mbps para tenancies elegibles desde 2020-12-15 | Candidato para la única entrada pública HTTPS | US$0 si Console confirma Always Free y se mantiene 10 Mbps | Revalidar elegibilidad antes de provisionar; no crear otro LB ni seleccionar capacidad pagada |
| Egreso de red | 10 TB/mes outbound Always Free | Sin volumen real medido | US$0 dentro de cuota | Paneles públicos pueden aumentar consultas/egreso; medir. No asumir precio cero por encima de cuota |
| Object Storage | 20 GB en cuenta Always Free only; en ciertas cuentas trial/paid se reparte 10 GB Standard + 10 GB Infrequent + 10 GB Archive | Ninguno en el primer corte | US$0 si elegible | La cantidad depende del estado de cuenta. No usar créditos de trial ni almacenar datos sensibles en un bucket público |
| VCN y firewall cloud | Recursos de red con cuotas Always Free documentadas; elegibilidad varía por tipo de cuenta | Una VCN, una subred, NSG por rol | Sin recurso adicional en el diseño mínimo si es elegible | Revisar cuota y precio al crear; no asumir que cada accesorio/servicio de red es gratuito |
| Public hostname / domain | `pages.dev` asignado a Pages; no se asume dominio propio incluido en OCI | Sin dominio propio hasta aprobación | Ninguna compra prevista | No pagar ni editar DNS. La demo Grafana queda privada si no hay hostname/TLS aprobado |

OCI publica que A1 se crea en la home region, que los límites de disponibilidad pueden impedir una VM y que instancias Always Free pueden ser reclamadas cuando se cumplen sus condiciones de inactividad. Una tarjeta, prueba promocional, servicio no elegible o uso fuera de cuota no es un control de costos. No se usará Free Trial para maquillar la estimación. Tampoco se recomienda cambiar a Pay As You Go.

### Presupuesto operativo propuesto

- VM 2 OCPU/8 GiB, un boot de 50 GB, un volumen de datos hasta 50 GB y como máximo un balanceador flexible de 10 Mbps solo si la consola confirma etiqueta Always Free.
- Mantener Prometheus 7 días / 2 GiB; rotar logs de contenedor y JSONL; alertar por 70 % y 85 % del disco y mantener al menos 20 GB libres.
- No crear GPU, IP pública de VM, balanceador extra, NAT Gateway, VPN, logging premium, bases de datos, backups adicionales o recursos no incluidos en la matriz sin un análisis de precio y autorización nuevo.
- Antes de cualquier aprovisionamiento futuro, comprobar límites, cuotas, estimador de costo y filtros en Console; configurar presupuestos/notificaciones si están disponibles. Un presupuesto/alerta notifica, no siempre impide una factura.

## Cloudflare Pages

El sitio estático no usa base de datos, Functions, almacenamiento de objetos, API privada, analytics ni dominio propio. Cloudflare publica para Free Pages 500 builds/mes, 1 build concurrente, timeout de 20 minutos, 20.000 archivos por sitio y 25 MiB como tamaño máximo de un asset. Las seis PNG actuales suman aproximadamente 1.41 MiB y cada una es mucho menor a 25 MiB. Esos límites deben verificarse de nuevo antes de conectar un proyecto.

| Elemento | Diseño | Riesgo/costo |
|---|---|---|
| Static assets | Astro output `dist`; 6 capturas | Sin server-side functions ni costo de cómputo de backend |
| Build | Menos de 1 build por PR; `pnpm build` | 500 builds/mes Free, una concurrente |
| Dominio | Subdominio automático `*.pages.dev` | No comprar dominio ni cambiar DNS |
| TLS | HTTPS administrado por Pages | No incluir claves TLS en Git |
| Preview deployments | PRs en rama feature, sujetos a control de branches | Tratar preview URLs como públicas; no introducir secretos o evidencia privada |
| Analytics | Deshabilitado / no agregado | Sin seguimiento de visitantes |

## Decisión sobre gasto

La única afirmación admisible de costo cero es “dentro de las cuotas Always Free publicadas y elegibles”. No se afirma que una cuenta pueda registrarse, conseguir A1 en su home region, conservar una instancia inactiva, obtener dominio de OCI sin cargo ni tener todos los servicios habilitados. Antes de recursos reales, el propietario debe verificar cada etiqueta y la estimación de costo; la tarea presente no crea un presupuesto o recurso remoto.

## Fuentes oficiales

- [OCI Always Free resources: A1, storage, egress, load balancer e idle reclaim](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [OCI Free Tier: Always Free y promoción de créditos separadas](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [Cloudflare Pages limits](https://developers.cloudflare.com/pages/platform/limits/)
