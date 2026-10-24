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
assert(!/-----BEGIN (?:RSA |EC |OPENSSH |PRIVATE )?PRIVATE KEY-----/.test(html), "the static page must not contain a private key");
assert(!/(?:password|token|api[_-]?key)\s*[:=]\s*["'][^"']{6,}["']/i.test(html), "the static page must not contain a credential assignment");

console.log(`Website build validation passed: ${screenshotNames.length} byte-identical screenshots, ${builtFiles.length} output paths, internal anchors and local assets verified.`);
