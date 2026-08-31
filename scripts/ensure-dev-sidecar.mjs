#!/usr/bin/env node
/**
 * Create a local inbound-engine sidecar so `tauri dev` can start.
 *
 * Production builds use the PyInstaller binary from `npm run sidecar`.
 * For day-to-day `npm run desktop:dev`, this writes a small wrapper that
 * execs the project venv (or python3) with absolute paths, so it still
 * works after Tauri copies the file into target/debug/.
 */
import { execSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const binaries = path.join(repo, "src-tauri", "binaries");
const launcher = path.join(repo, "edge", "launcher.py");
const venvPython = path.join(repo, "edge", ".venv", "bin", "python");
const python = existsSync(venvPython) ? venvPython : "python3";

function hostTriple() {
  const fromEnv = process.env.TAURI_ENV_TARGET_TRIPLE?.trim();
  if (fromEnv) return fromEnv;
  return execSync("rustc --print host-tuple", { encoding: "utf8" }).trim();
}

function isFrozenBinary(file) {
  if (!existsSync(file)) return false;
  const header = readFileSync(file).subarray(0, 4);
  const elf = header[0] === 0x7f && header[1] === 0x45 && header[2] === 0x4c && header[3] === 0x46;
  const pe = header[0] === 0x4d && header[1] === 0x5a;
  const macho = header[0] === 0xcf && header[1] === 0xfa;
  return elf || pe || macho;
}

const triple = hostTriple();
const ext = process.platform === "win32" || triple.includes("windows") ? ".exe" : "";
const dest = path.join(binaries, `inbound-engine-${triple}${ext}`);

mkdirSync(binaries, { recursive: true });

if (isFrozenBinary(dest)) {
  console.log(`Using frozen sidecar: ${dest}`);
  process.exit(0);
}

const script = `#!/usr/bin/env bash
set -euo pipefail
exec ${JSON.stringify(python)} ${JSON.stringify(launcher)} "$@"
`;

writeFileSync(dest, script, { mode: 0o755 });
chmodSync(dest, 0o755);
console.log(`Dev sidecar wrapper -> ${dest}`);
console.log(`Engine: ${python} ${launcher}`);
