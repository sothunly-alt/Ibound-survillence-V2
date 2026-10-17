#!/usr/bin/env python3
"""Create a clean, production-ready ZIP file for customer download.

Excludes developer artifacts (.git, virtual environments, caches, node_modules,
temp media), customer-privacy data (face photos, proof stills, event databases),
and any real credentials (.env, the developer's gitignored config.yaml), then
injects the tracked customer config (``edge/config.champei.yaml``) as the
``edge/config.yaml`` the engine boots with.
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_ZIP = ROOT_DIR / "Champei_Spa_Intelligence.zip"

# Config the shipped engine should boot with. Deployed as edge/config.yaml.
CUSTOMER_CONFIG_SRC = ROOT_DIR / "edge" / "config.champei.yaml"
CUSTOMER_CONFIG_DEST = "edge/config.yaml"

EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    ".vscode",
    ".tempmediaStorage",
    ".user_uploaded",
    "test_tracking_outputs",
    "artifacts",
    # Rust/Tauri build output (can exceed 1 GB) and other build trees.
    "target",
    # Customer privacy / runtime data must never ship.
    "faces",
    "proofs",
    "logs",
    "runs",
    "dataset",
    "build",
    "dist-sidecar",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".log",
    ".db",
    ".db-wal",
    ".db-shm",
}

# Exact filenames that must never be included (credentials / live state).
EXCLUDE_FILES = {
    ".env",
    ".env.local",
    "events.db",
    "test.db",
    "edge/config.yaml",
    "session.json",
}


def _rel(path: Path) -> str:
    return path.relative_to(ROOT_DIR).as_posix()


def make_customer_zip():
    print(f"[Packaging] Scanning project directory: {ROOT_DIR}")
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()

    if not CUSTOMER_CONFIG_SRC.is_file():
        print(f"[ERROR] Customer config not found: {CUSTOMER_CONFIG_SRC}")
        print("        Refusing to build a package with no deployable config.")
        sys.exit(1)

    included_count = 0
    excluded_count = 0
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(ROOT_DIR):
            dirs[:] = [
                d for d in dirs
                if d not in EXCLUDE_DIRS and not d.endswith(".venv")
            ]

            rel_root = Path(root).relative_to(ROOT_DIR)

            # Skip root output zip itself if in walking path
            if rel_root.as_posix().startswith("Champei_Spa_Intelligence.zip"):
                continue

            for file in files:
                file_path = Path(root) / file
                rel = _rel(file_path)

                if file_path == OUTPUT_ZIP:
                    continue
                if rel == CUSTOMER_CONFIG_DEST:
                    # Replaced below by the deployed customer config.
                    excluded_count += 1
                    continue
                if rel in EXCLUDE_FILES:
                    excluded_count += 1
                    continue
                if any(file.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
                    excluded_count += 1
                    continue
                if file.startswith(".DS_Store"):
                    continue

                arcname = (rel_root / file).as_posix()
                zf.write(file_path, arcname)
                included_count += 1

        # Deploy the customer config as the engine's edge/config.yaml.
        zf.write(CUSTOMER_CONFIG_SRC, CUSTOMER_CONFIG_DEST)
        included_count += 1
        print(f"[Packaging] Deployed {_rel(CUSTOMER_CONFIG_SRC)} -> {CUSTOMER_CONFIG_DEST}")

    size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print("\n[Packaging] SUCCESS!")
    print(f"📦 Created Release ZIP: {OUTPUT_ZIP.name} ({size_mb:.2f} MB)")
    print(f"📄 Total Files Included: {included_count}")
    print(f"🚫 Sensitive/Runtime Files Excluded: {excluded_count}")
    print(f"📁 Path: {OUTPUT_ZIP.resolve()}")


if __name__ == "__main__":
    make_customer_zip()
