#!/usr/bin/env python3
"""Build the inbound-engine sidecar and install it for Tauri.

Usage (from the repository root or this directory):

    python edge/build_sidecar.py
    python edge/build_sidecar.py --target x86_64-unknown-linux-gnu

The resulting binary is copied to::

    src-tauri/binaries/inbound-engine-<target-triple>[.exe]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

EDGE = Path(__file__).resolve().parent
REPO = EDGE.parent
SPEC = EDGE / "inbound-engine.spec"
WEIGHTS = EDGE / "yolo11n-pose.pt"
BINARIES = REPO / "src-tauri" / "binaries"


def _run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def detect_target_triple() -> str:
    env = os.environ.get("TAURI_ENV_TARGET_TRIPLE", "").strip()
    if env:
        return env
    rustc = shutil.which("rustc")
    if rustc:
        try:
            out = subprocess.check_output(
                [rustc, "--print", "host-tuple"], text=True
            ).strip()
            if out:
                return out
        except (subprocess.CalledProcessError, OSError):
            pass
    # Fallback for hosts without rustc (should not happen in CI).
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin":
        import platform

        return (
            "aarch64-apple-darwin"
            if platform.machine() == "arm64"
            else "x86_64-apple-darwin"
        )
    return "x86_64-unknown-linux-gnu"


def ensure_weights() -> None:
    if WEIGHTS.exists() and WEIGHTS.stat().st_size > 1_000_000:
        print(f"Using existing weights: {WEIGHTS}", flush=True)
        return
    print("Downloading yolo11n-pose.pt via ultralytics…", flush=True)
    from ultralytics import YOLO

    cwd = os.getcwd()
    os.chdir(EDGE)
    try:
        YOLO("yolo11n-pose.pt")
    finally:
        os.chdir(cwd)
    if not WEIGHTS.exists():
        # Ultralytics may drop the file in the current working directory.
        fallback = Path.cwd() / "yolo11n-pose.pt"
        if fallback.exists():
            shutil.copy2(fallback, WEIGHTS)
    if not WEIGHTS.exists():
        raise SystemExit(
            "yolo11n-pose.pt was not downloaded. Place the weights in edge/ and retry."
        )


def exe_name() -> str:
    return "inbound-engine.exe" if sys.platform == "win32" else "inbound-engine"


def sidecar_name(target: str) -> str:
    ext = ".exe" if sys.platform == "win32" or target.endswith("windows-msvc") else ""
    return f"inbound-engine-{target}{ext}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the inbound-engine Tauri sidecar")
    parser.add_argument(
        "--target",
        default="",
        help="Rust target triple (default: rustc --print host-tuple)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Do not download YOLO weights if missing",
    )
    args = parser.parse_args()

    if not args.skip_download:
        ensure_weights()
    elif not WEIGHTS.exists():
        print("WARNING: edge/yolo11n-pose.pt is missing; the sidecar will download at runtime.", flush=True)

    dist = REPO / "dist-sidecar"
    work = REPO / "build" / "pyinstaller"
    dist.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            str(dist),
            "--workpath",
            str(work),
            str(SPEC),
        ],
        cwd=str(REPO),
    )

    built = dist / exe_name()
    if not built.exists():
        raise SystemExit(f"PyInstaller did not produce {built}")

    target = args.target.strip() or detect_target_triple()
    BINARIES.mkdir(parents=True, exist_ok=True)
    dest = BINARIES / sidecar_name(target)
    shutil.copy2(built, dest)
    dest.chmod(dest.stat().st_mode | 0o111)
    print(f"Sidecar installed: {dest}", flush=True)


if __name__ == "__main__":
    main()
