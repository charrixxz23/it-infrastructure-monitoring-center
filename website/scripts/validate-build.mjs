import assert from "node:assert/strict";
import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(projectRoot, "..");
const outputRoot = path.join(projectRoot, "dist");
const htmlPath = path.join(outputRoot, "index.html");
const html = await readFile(htmlPath, "utf8");

assert.match(html, /<html[^>]+lang="es"/i, "built homepage should declare its initial language");
assert.match(html, /IT Infrastructure/);
assert.match(html, /Monitoring Center/);
assert.match(html, /Cloud deployment/);
assert.match(html, /data-locale-button="en"/);
assert.equal((html.match(/<h1\b/gi) ?? []).length, 1, "homepage should have a single primary heading");
assert.match(html, /id="real-network"/, "the real-world network section should be present");
assert.match(html, /Real-World Network Monitoring/, "the English network heading should be present");
assert.match(html, /Monitoreo de red real/, "the Spanish network heading should be present");
assert.match(html, /BlackboxExporterDown/, "the preparation incident should be documented");
assert.match(html, /25 min 15 s/, "the documented incident duration should be visible");
assert.match(html, /data-image-src="\/screenshots\/07-home-network-monitoring\.png"/, "the authentic dashboard should use the accessible image viewer");
assert.match(html, /data-image-alt-en="Authentic Home Network Monitoring dashboard screenshot/, "the dashboard viewer should have English alternative text");
assert.match(html, /data-image-alt-es="Captura auténtica del dashboard Home Network Monitoring/, "the dashboard viewer should have Spanish alternative text");
assert.match(html, /data-zoom-out/, "the screenshot viewer should include a zoom-out control");
assert.match(html, /data-zoom-in/, "the screenshot viewer should include a zoom-in control");
assert.match(html, /data-zoom-reset/, "the screenshot viewer should include a reset control");
assert.match(html, /data-aria-es="Aumentar zoom"[^>]*data-aria-en="Zoom in"/, "zoom controls should have Spanish and English accessible names");
assert.match(html, /aria-keyshortcuts="Shift\+Equal - 0"/, "the image viewport should advertise keyboard zoom shortcuts");
assert.match(html, /double-tap toggles between 1× and 2×/, "touch zoom instructions should be available in English");

const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]));
for (const match of html.matchAll(/\bhref="#([^\"]+)"/g)) {
  assert(ids.has(match[1]), `missing in-page target #${match[1]}`);
}

for (const match of html.matchAll(/<img\b([^>]+)>/gi)) {
  const src = /\bsrc="([^"]*)"/i.exec(match[1])?.[1];
  // The dialog's image is intentionally empty until a user opens a screenshot;
  // the script fills its source and localized alt text before showing the dialog.
  if (!src && /\bid="viewer-image"/.test(match[1])) continue;
  assert.match(match[1], /\balt="[^"]+"/i, "each image must have a non-empty accessible description");
  assert(src, "each image must have a source");
  assert(!/^https?:/i.test(src), `external images are not allowed: ${src}`);
  const filePath = path.join(outputRoot, decodeURIComponent(src.replace(/^\//, "")));
  assert((await stat(filePath)).isFile(), `image asset is missing from the build: ${src}`);
}

const screenshotNames = [
  "01-infrastructure-overview.png",
  "02-docker-container-monitoring.png",
  "03-prometheus-targets.png",
  "04-prometheus-alert-rules.png",
  "05-alertmanager-no-active-groups.png",
  "06-github-actions-ci.png",
  "07-home-network-monitoring.png",
];
for (const name of screenshotNames) {
  const original = await readFile(path.join(repositoryRoot, "screenshots", name));
  const included = await readFile(path.join(outputRoot, "screenshots", name));
  assert.deepEqual(included, original, `screenshot bytes changed in the site build: ${name}`);
}

const builtFiles = await readdir(outputRoot, { recursive: true });
assert(builtFiles.includes("404.html"), "a static 404 page should be built");
assert(builtFiles.includes("_headers"), "Cloudflare Pages security headers should be part of the build");
assert(!/127\.0\.0\.1:(?:3000|9090|9093|9464)/.test(html), "the public site must not point to local monitoring endpoints");
assert(!/\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b/.test(html), "the public page must not include private IPv4 addresses");
assert(!/-----BEGIN (?:RSA |EC |OPENSSH |PRIVATE )?PRIVATE KEY-----/.test(html), "the static page must not contain a private key");
assert(!/(?:password|token|api[_-]?key)\s*[:=]\s*["'][^"']{6,}["']/i.test(html), "the static page must not contain a credential assignment");

const networkDocumentation = [
  "docs/home-network-monitoring.md",
  "docs/runbooks/home-network.md",
  "docs/incidents/blackbox-exporter-preparation.md",
  "docs/templates/home-network-postmortem.md",
  "config/grafana/dashboards/home-network-monitoring.json",
  "config/prometheus/rules/home-network.yml",
];
for (const relativePath of networkDocumentation) {
  assert((await stat(path.join(repositoryRoot, relativePath))).isFile(), `network documentation target is missing: ${relativePath}`);
}

console.log(`Website build validation passed: ${screenshotNames.length} byte-identical screenshots, ${builtFiles.length} output paths, network documentation links, internal anchors, accessibility descriptions, privacy checks, and local assets verified.`);
