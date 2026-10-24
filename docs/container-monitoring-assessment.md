# Evaluación de seguridad para cAdvisor (Fase 3A)

Fecha: 2026-10-24 (hora de Panamá).

## Decisión

La incorporación de cAdvisor queda **detenida por el límite de seguridad definido para la Fase 3A**. Esta evaluación no añadió un servicio cAdvisor ni un target o dashboard basados en cAdvisor. La alternativa distinta de Fase 3B ejecuta un proceso Python de Windows enlazado a loopback; su arquitectura, riesgos y validación están en [container-exporter.md](container-exporter.md). La decisión de no entregar los sockets al stack Docker sigue vigente.

La condición para detenerse se cumple: en este entorno, cAdvisor no obtuvo identidad de contenedor y métricas de red individuales usando solo montajes de observación. Para obtenerlas tuvo que acceder tanto al socket de Docker como al de containerd. Esos sockets exponen APIs de control del motor; montarlos como `:ro` no limita los métodos que se pueden solicitar a la API. La configuración requeriría un acceso de privilegio equivalente al control del daemon, además de montajes del host (`/` y `/sys`) que pueden revelar metadatos del sistema.

## Entorno y método

- Docker Desktop 4.96.3.244472, Docker Engine 29.9.2, backend WSL2 y cgroups v2.
- Se inspeccionó la compatibilidad con cgroups v2 y se probó cAdvisor v0.60.6, fijado durante la evaluación por digest OCI `sha256:b8e7d1093144fd088f425ff003d75a4aa405de075db78dae3bc563730b1bd07a`.
- Las sondas fueron contenedores temporales creados por esta evaluación: red interna existente, sin puertos publicados, sin `privileged`, sin PID del host, filesystem raíz de solo lectura, `/tmp` temporal, capacidades descartadas y `no-new-privileges`.
- Se comparó la salida de métricas al variar montajes de `/sys`, `/`, socket Docker y socket containerd. No se copian a esta documentación ni se conservan logs de cAdvisor o contenido sensible del motor.

## Evidencia observada

| Acceso probado | Resultado de métricas | Resultado de identidad | Decisión |
|---|---|---|---|
| `/sys` y cgroups, sin sockets | CPU y memoria por ID de cgroup; red agregada en la raíz | Sin nombre ni etiquetas de Compose | No satisface el monitoreo individual solicitado |
| `/` y `/sys`, sin sockets | CPU y memoria por cgroup; series de red sin atribución individual fiable | ID de cgroup opaco | No satisface el dashboard solicitado |
| Socket Docker sin socket containerd | La fábrica Docker no pudo inicializarse porque no estaba disponible el socket containerd; no hubo métricas Docker individuales útiles | Sin etiquetas de proyecto/servicio utilizables | No satisface el requisito |
| Socket containerd sin socket Docker | No entregó series individuales identificables de red ni etiquetas del proyecto | Sin identidad Docker utilizable | No satisface el requisito |
| Ambos sockets, `/` y `/sys` | cAdvisor quedó saludable. CPU, memoria y última observación incluyeron cinco series con etiqueta del proyecto; RX/TX incluyeron ocho series del proyecto porque la red genera series por interfaz | Las etiquetas reales permitieron identificar cinco contenedores del Compose por proyecto, servicio, nombre e ID | Funciona técnicamente, pero requiere acceso de control a dos APIs del motor; se detuvo por seguridad |

La prueba con ambos sockets identificó métricas para los contenedores del proyecto, pero no se considera una implementación. Las métricas `container_last_seen` y las series de cAdvisor serían observaciones de telemetría, no un inventario autoritativo del estado Docker.

Las sondas temporales `itmc-cadvisor-*-probe` se retiraron al terminar. No se crearon volúmenes. No se detuvo ni modificó ningún servicio de los proyectos existentes.

## Cambios de esta evaluación

En la evaluación inicial de Fase 3A solo cambió documentación. La Fase 3B posterior agregó un job Prometheus y un JSON independiente de dashboard para el exporter host-side, sin modificar Compose, redes, volúmenes ni el dashboard de infraestructura. No hay integración cAdvisor.

## Alternativas antes de reabrir la implementación

1. Ejecutar el colector en un host Linux de monitoreo separado, con una frontera de acceso controlada y sin exponer los sockets del Docker Desktop personal.
2. Evaluar un proxy/API intermedia con lista permitida estricta para las operaciones que cAdvisor necesite. Debe comprobarse que limite de verdad métodos y rutas de Docker y containerd; una etiqueta `:ro` en el bind mount no es ese control. No se ha probado ni desplegado un proxy en este proyecto.
3. Si no existe una frontera de acceso verificable, mantener el monitoreo de infraestructura actual y recopilar métricas desde aplicaciones/servicios que las expongan sin otorgar control del daemon.

Para la ruta cAdvisor se mantiene el bloqueo por permisos excesivos. La alternativa Fase 3B ejecutada fuera de Docker y sus privilegios residuales se describen en [container-exporter.md](container-exporter.md). No se recomienda montar directamente los dos sockets en el stack actual.

## Referencias técnicas

- [Ejecución y montajes de cAdvisor](https://github.com/google/cadvisor/blob/master/docs/running.md)
- [Opciones de ejecución de cAdvisor](https://github.com/google/cadvisor/blob/master/docs/runtime_options.md)
- [Protección del socket del daemon Docker](https://docs.docker.com/engine/security/protect-access/)
- [Superficie de ataque del daemon Docker](https://docs.docker.com/engine/security/)
- [Backend WSL2 de Docker Desktop](https://docs.docker.com/desktop/features/wsl/)
