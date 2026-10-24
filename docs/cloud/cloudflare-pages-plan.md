# Plan de publicación del portafolio en Cloudflare Pages

**Estado:** preparación local; ningún proyecto Pages, cuenta, token ni despliegue ha sido creado. **Verificado:** 2026-10-24.

## Aplicación y límites

`website/` contiene un sitio Astro estático bilingüe. El build produce `website/dist/`; no usa backend, funciones server-side, datos en vivo, analítica, variables secretas ni APIs del laboratorio. Las seis capturas se copian sin modificar desde `screenshots/`. Las cifras de esas capturas son evidencia histórica, no telemetría actual.

Cloudflare Pages admite Astro con integración Git y despliegues de vista previa para cambios. La configuración prevista cuando haya autorización es:

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
| Hostname inicial | Subdominio asignado `*.pages.dev`, solo tras revisión de publicación |

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

La versión actual de Astro exige Node `>=22.23.4`; el runtime de desarrollo se fija a Node 24.22.1 y el gestor queda fijado en `package.json`. Confirmar en la pantalla de configuración de Pages que la imagen de build usa dichas versiones antes de conectar el repositorio. No asumir que el runtime predeterminado de Cloudflare coincide.

## Flujo de autorización futura

1. El propietario revisa el contenido público, especialmente las capturas y límites de seguridad.
2. Desde la cuenta autorizada, crea el proyecto Pages mediante integración Git y selecciona únicamente el repositorio esperado.
3. Configura directorio raíz `website`, comando `pnpm build` y directorio `dist`; fija versiones y revisa permisos OAuth solicitados.
4. Configura `main` como rama de producción y mantén previews de ramas/PR como revisiones temporales; la vista previa sigue siendo potencialmente pública.
5. Publica primero una vista previa y revisa idioma, imágenes, enlaces, headers y salida de build.
6. Solo tras aprobación explícita permite la producción. Protege `main` según la política del repositorio y no automatices deploy desde forks o PR no confiables.
7. Si un build publicado causa una regresión, selecciona una implementación anterior desde Pages. Verifica el rollback y el estado del dominio en consola.

No hace falta configurar credenciales OCI, Docker, Grafana o Prometheus en Pages. No habilitar Functions para el portal. Tratar URLs de preview como públicas: cambios de PR y capturas deben ser aptos para publicación antes de subirlos.

## Cuotas y riesgos

Cloudflare publica para Free Pages 500 builds mensuales, una compilación concurrente, 20 minutos por build, hasta 20.000 archivos y 25 MiB por asset. Los límites y disponibilidad pueden cambiar; verificarlos justo antes de crear el proyecto. El portal actual tiene seis imágenes estáticas y no necesita workers ni almacenamiento de métricas.

El dominio `pages.dev` no vuelve privado el contenido. El uso de un subdominio público implica revisión de las seis capturas, metadatos, repositorio y documentos antes del primer deploy. No se configura dominio propio ni DNS en esta fase.

## Fuentes oficiales

- [Cloudflare: guía Astro para Pages](https://developers.cloudflare.com/pages/framework-guides/deploy-an-astro-site/)
- [Cloudflare Pages: configuración de builds](https://developers.cloudflare.com/pages/configuration/build-configuration/)
- [Cloudflare Pages: build image y variables de versión](https://developers.cloudflare.com/pages/configuration/build-image/)
- [Cloudflare Pages: límites de plataforma](https://developers.cloudflare.com/pages/platform/limits/)
- [Cloudflare Pages: rollbacks](https://developers.cloudflare.com/pages/configuration/rollbacks/)
- [Astro: instalación y requisitos de Node](https://docs.astro.build/en/install-and-setup/)
- [Astro CLI: desactivar telemetría](https://docs.astro.build/en/reference/cli-reference/#astro-telemetry)
