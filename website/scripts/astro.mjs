import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const astroCli = path.join(projectRoot, "node_modules", "astro", "bin", "astro.mjs");
const result = spawnSync(process.execPath, [astroCli, ...process.argv.slice(2)], {
  cwd: projectRoot,
  env: { ...process.env, ASTRO_TELEMETRY_DISABLED: "1" },
  stdio: "inherit",
});

if (result.error) {
  console.error(`Could not start the local Astro CLI: ${result.error.message}`);
}

process.exit(result.status ?? 1);
