# Plan de publicación del portafolio en Cloudflare Pages

**Estado:** el portal se desplegó correctamente y está publicado en [https://it-infrastructure-monitoring-center.pages.dev/](https://it-infrastructure-monitoring-center.pages.dev/). La URL respondió HTTP 200 el 2026-10-24. El sitio contiene capturas auténticas y documentación; no presenta métricas en vivo.

## Aplicación y límites

`website/` contiene un sitio Astro estático bilingüe. El build produce `website/dist/`; no usa backend, funciones server-side, datos en vivo, analítica, variables secretas ni APIs del laboratorio. Las seis capturas se copian sin modificar desde `screenshots/`. Las cifras de esas capturas son evidencia histórica, no telemetría actual.

Cloudflare Pages admite Astro con integración Git y despliegues de vista previa para cambios. Esta tabla conserva la configuración recomendada del build; no se auditó la configuración de la cuenta Pages durante esta actualización.

| Ajuste | Valor propuesto |
|---|---|
| Framework preset | Astro |
| Root directory | `website` |
| Build command | `pnpm build` |
| Build output directory | `dist` |
| Node | `24.22.1` desde `website/.nvmrc` / `NODE_VERSION` |
| pnpm | `11.25.0` desde `PNPM_VERSION` |
| Astro CLI telemetry | `ASTRO_TELEMETRY_DISABLED=1` |
| Variables secretas | Ninguna |
| Hostname público | `https://it-infrastructure-monitoring-center.pages.dev/` |

Estructura relevante del sitio:

```text
website/
├── public/                  # headers, favicon y capturas auténticas
├── scripts/                 # validador del build estático
├── src/pages/               # home y 404
├── src/scripts/              # selector ES/EN y visor de capturas
├── src/styles/               # estilos responsive
├── .nvmrc
├── package.json
└── pnpm-lock.yaml
```

### Comandos reproducibles

```powershell
Set-Location website
pnpm install --frozen-lockfile
pnpm dev
# En otra terminal, dentro de website:
pnpm check
pnpm build
pnpm preview
```

El desarrollo se sirve en `127.0.0.1:4321`; la salida estática es `dist/`. Pages usaría `NODE_VERSION=24.22.1`, `PNPM_VERSION=11.25.0` y `ASTRO_TELEMETRY_DISABLED=1`; no hay configuración de aplicación ni secretos. Los scripts del proyecto pasan el opt-out al CLI sin cambiar preferencias globales del equipo.

La versión actual de Astro exige Node `>=22.23.4`; el runtime de desarrollo se fija a Node 24.22.1 y el gestor queda fijado en `package.json`. Antes de modificar Pages, confirmar que la imagen de build usa dichas versiones. No asumir que el runtime predeterminado de Cloudflare coincide.

## Mantenimiento del deployment publicado

El portal ya está publicado en la URL indicada arriba. Antes de futuras actualizaciones, revisa el contenido público —incluidas capturas y límites de seguridad— y comprueba la configuración existente en Pages; no crees un proyecto duplicado. Valida cualquier preview, incluidos idioma, imágenes, enlaces, headers y resultado del build, antes de autorizar una nueva publicación. Si una actualización causa una regresión, selecciona una implementación anterior desde Pages y verifica el rollback.

No hace falta configurar credenciales OCI, Docker, Grafana o Prometheus en Pages. No habilitar Functions para el portal. Tratar URLs de preview como públicas: cambios de PR y capturas deben ser aptos para publicación antes de subirlos.

## Cuotas y riesgos

Cloudflare publica para Free Pages 500 builds mensuales, una compilación concurrente, 20 minutos por build, hasta 20.000 archivos y 25 MiB por asset. Los límites y disponibilidad pueden cambiar; verificarlos antes de futuros cambios de hosting. El portal actual tiene seis imágenes estáticas y no necesita workers ni almacenamiento de métricas.

El dominio `pages.dev` no vuelve privado el contenido. La publicación actual es pública; revisa las seis capturas, metadatos, repositorio y documentos antes de cada futura actualización. No se configura dominio propio ni DNS en esta tarea.

## Fuentes oficiales

- [Cloudflare: guía Astro para Pages](https://developers.cloudflare.com/pages/framework-guides/deploy-an-astro-site/)
- [Cloudflare Pages: configuración de builds](https://developers.cloudflare.com/pages/configuration/build-configuration/)
- [Cloudflare Pages: build image y variables de versión](https://developers.cloudflare.com/pages/configuration/build-image/)
- [Cloudflare Pages: límites de plataforma](https://developers.cloudflare.com/pages/platform/limits/)
- [Cloudflare Pages: rollbacks](https://developers.cloudflare.com/pages/configuration/rollbacks/)
- [Astro: instalación y requisitos de Node](https://docs.astro.build/en/install-and-setup/)
- [Astro CLI: desactivar telemetría](https://docs.astro.build/en/reference/cli-reference/#astro-telemetry)
