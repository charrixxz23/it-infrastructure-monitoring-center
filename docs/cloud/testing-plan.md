# Fase 5 — Pruebas y criterios de aceptación

Esta fase prepara el portal y documenta una arquitectura cloud. No afirma un despliegue ni una prueba de OCI. Las validaciones locales no modifican Docker Compose ni inician o detienen servicios del laboratorio.

## Validación local del portal

Requisitos: Git, Node `24.22.1` (o una versión compatible con `website/package.json`) y pnpm `11.25.0`.

```powershell
Set-Location website
pnpm install --frozen-lockfile
pnpm check
pnpm build
pnpm test
pnpm audit --audit-level high
```

`pnpm test` valida el HTML producido: idioma, H1, anclas locales, atributos `alt`, assets locales, copia byte a byte de las seis capturas, 404, headers y ausencia de endpoints locales o asignaciones de credenciales en el HTML. El script no sustituye una auditoría de seguridad completa.

La lista directa está fijada en `package.json` y `pnpm-lock.yaml`: Astro 7.4.2 (MIT), `@astrojs/check` 0.9.12 (MIT) y TypeScript 6.0.2 (Apache-2.0). `pnpm licenses list --dev` encontró metadatos declarados para las dependencias transitivas (MIT, ISC, Apache-2.0, BlueOak-1.0.0, BSD-2-Clause, BSD-3-Clause, MPL-2.0, CC0-1.0 y 0BSD); `pnpm audit --audit-level high` no reportó vulnerabilidades conocidas. Los scripts usan `ASTRO_TELEMETRY_DISABLED=1` para el CLI de compilación; el sitio tampoco incorpora analítica ni trackers. Esto es una revisión técnica, no una conclusión legal.

Para revisar el sitio sin exponer el servidor a la LAN:

```powershell
pnpm preview --host 127.0.0.1
```

Abrir `http://127.0.0.1:4321/` localmente y comprobar ambas lenguas, navegación, diálogos de capturas, layout móvil y desktop, enlaces externos y consola del navegador. El proceso escucha solo en loopback.

## Documentación y repositorio

Desde la raíz:

```powershell
python scripts/ci/validate_cloud_docs.py
git diff --check
git status --short
git diff --name-only
git check-ignore -v .env data website/node_modules website/.astro website/dist
```

El validador de documentación comprueba enlaces relativos existentes y estructura de bloques Mermaid; no reemplaza un renderer completo de Mermaid. Los validadores existentes del proyecto pueden ejecutarse sin levantar contenedores:

```powershell
python scripts/ci/validate_dashboards.py
python scripts/ci/validate_prometheus.py
python scripts/ci/validate_compose.py
python scripts/ci/check_repo_hygiene.py
```

El workflow existente conserva los jobs Python 3.10/3.13, static security y configuración de monitoreo. Esta rama añade un job independiente de Astro con Node/pnpm fijados, instalación frozen, `check`, build, validación de assets y auditoría; también ejecuta el validador de documentación en el job static-security. El `main` inspeccionado tenía cuatro jobs exitosos antes de estos cambios. La rama local aún no tiene una ejecución remota de Actions.

## Pruebas que requieren autorización o infraestructura cloud

1. Revisión de privacidad de las capturas, enlaces y documentación pública.
2. Confirmación de cuotas, región de origen y capacidad Always Free dentro de una cuenta OCI autorizada.
3. Pruebas nativas ARM64 de Compose cloud separado, imágenes, salud, persistencia, retención, alertas y recuperación.
4. Revisión de NSG/firewall desde origen permitido y denegado; confirmar que solo la ruta HTTPS aprobada es pública.
5. Verificación de que Prometheus, Alertmanager, exporters, webhook y API Docker son inalcanzables desde Internet.
6. Construcción de Pages preview con versiones fijadas; revisar resultado y rollback desde la cuenta Cloudflare autorizada.
7. Validación de hostname, TLS, headers y política de acceso antes de anunciar dashboard en vivo.

No crear recursos, cuentas, DNS, tareas programadas, workflows de deploy ni credenciales como parte de estas pruebas documentales.
