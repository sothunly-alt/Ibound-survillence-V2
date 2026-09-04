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
import { chmodSync, copyFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const binaries = path.join(repo, "src-tauri", "binaries");
const launcher = path.join(repo, "edge", "launcher.py");
const venvPython = path.join(repo, "edge", ".venv", "bin", "python");
const python = existsSync(venvPython) ? venvPython : "python3";

function syncLinuxDesktopIcons() {
  if (process.platform !== "linux") return;
  const home = process.env.HOME;
  if (!home) return;
  const iconsHicolor = path.join(home, ".local", "share", "icons", "hicolor");
  const appsDir = path.join(home, ".local", "share", "applications");
  const iconSrc = path.join(repo, "src-tauri", "icons");

  const sizeMap = {
    "32x32": "32x32.png",
    "64x64": "64x64.png",
    "128x128": "128x128.png",
    "256x256": "128x128@2x.png",
    "512x512": "icon.png",
  };

  try {
    for (const [dirName, srcFile] of Object.entries(sizeMap)) {
      const srcPath = path.join(iconSrc, srcFile);
      if (!existsSync(srcPath)) continue;
      const targetDir = path.join(iconsHicolor, dirName, "apps");
      mkdirSync(targetDir, { recursive: true });
      copyFileSync(srcPath, path.join(targetDir, "inbound-surveillance.png"));
    }

    mkdirSync(appsDir, { recursive: true });
    const desktopFileContent = `[Desktop Entry]
Categories=Utility;Development;
Comment=Inbound Surveillance desktop application
Exec=npm run desktop:dev
StartupWMClass=inbound-surveillance
Icon=inbound-surveillance
Name=Inbound Surveillance
Terminal=false
Type=Application
`;
    writeFileSync(path.join(appsDir, "inbound-surveillance.desktop"), desktopFileContent);
    writeFileSync(path.join(appsDir, "Inbound Surveillance.desktop"), desktopFileContent);

    try { execSync(`gtk-update-icon-cache -f -t ${JSON.stringify(iconsHicolor)}`, { stdio: "ignore" }); } catch (_) {}
    try { execSync(`update-desktop-database ${JSON.stringify(appsDir)}`, { stdio: "ignore" }); } catch (_) {}
  } catch (_) {}
}

function ensureIcons() {
  const possibleSources = [
    path.join(repo, "inb_surveillance.jpg"),
    path.join(repo, "INB Surveillance.jpg"),
    path.join(repo, "inb_surveillance.png"),
    path.join(repo, "INB Surveillance.png"),
    path.join(repo, "app-icon.png"),
  ];
  const source = possibleSources.find((p) => existsSync(p));
  if (!source) return;

  const inbSurveillance = path.join(repo, "INB Surveillance.jpg");
  const inbSurveillanceLower = path.join(repo, "inb_surveillance.jpg");
  if (existsSync(inbSurveillance) && !existsSync(inbSurveillanceLower)) {
    try { copyFileSync(inbSurveillance, inbSurveillanceLower); } catch (_) {}
  } else if (existsSync(inbSurveillanceLower) && !existsSync(inbSurveillance)) {
    try { copyFileSync(inbSurveillanceLower, inbSurveillance); } catch (_) {}
  }

  const iconPng = path.join(repo, "src-tauri", "icons", "icon.png");
  const sourceMtime = statSync(source).mtimeMs;
  const iconMtime = existsSync(iconPng) ? statSync(iconPng).mtimeMs : 0;

  if (!existsSync(iconPng) || sourceMtime > iconMtime) {
    console.log(`[icons] Detected launcher image: ${path.basename(source)}`);
    console.log(`[icons] Compiling Tauri icon bundle...`);
    try {
      execSync(`npx tauri icon ${JSON.stringify(source)}`, {
        cwd: repo,
        stdio: "inherit",
      });
      console.log(`[icons] Successfully compiled Tauri icons.`);
    } catch (err) {
      console.warn(`[icons] Warning: failed to run tauri icon: ${err.message}`);
    }
  }

  syncLinuxDesktopIcons();
}

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

ensureIcons();

if (process.platform === "linux") {
  // Applied here for documentation; run-tauri.mjs and src-tauri/src/lib.rs
  // are what the WebKitGTK process actually inherits.
  process.env.WEBKIT_DISABLE_DMABUF_RENDERER ||= "1";
  process.env.WEBKIT_DISABLE_COMPOSITING_MODE ||= "1";
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
