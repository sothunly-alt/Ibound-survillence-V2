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
VEHICLE_WEIGHTS = EDGE / "yolo11n.pt"
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
    improved = EDGE / "yolo11n_improved.pt"
    if improved.exists() and improved.stat().st_size > 1_000_000:
        print(f"Using improved weights: {improved}", flush=True)

    for model_name, target_file in [
        ("yolo11n-pose.pt", WEIGHTS),
        ("yolo11n.pt", VEHICLE_WEIGHTS),
    ]:
        if target_file.exists() and target_file.stat().st_size > 1_000_000:
            print(f"Using existing weights: {target_file}", flush=True)
            continue
        print(f"Downloading {model_name} via ultralytics…", flush=True)
        from ultralytics import YOLO

        cwd = os.getcwd()
        os.chdir(EDGE)
        try:
            YOLO(model_name)
        finally:
            os.chdir(cwd)
        if not target_file.exists():
            fallback = Path.cwd() / model_name
            if fallback.exists():
                shutil.copy2(fallback, target_file)
        if not target_file.exists():
            raise SystemExit(
                f"{model_name} was not downloaded. Place the weights in edge/ and retry."
            )


def exe_name() -> str:
    return "inbound-engine.exe" if sys.platform == "win32" else "inbound-engine"


REQUIRED_PYZ_MODULES = (
    "ai_auditor",
    "occupancy",
    "telegram_link",
    "vehicle",
    "paths",
    "db",
    "face_id",
    "adapters.video_file",
)


def _assert_modules_bundled(work: Path) -> None:
    """Fail the sidecar build if PyInstaller dropped a first-party module."""
    engine_dir = work / "inbound-engine"
    toc_blobs: list[str] = []
    for name in ("PYZ-00.toc", "PKG-00.toc", "Analysis-00.toc"):
        path = engine_dir / name
        if path.is_file():
            toc_blobs.append(path.read_text(encoding="utf-8", errors="replace"))
    blob = "\n".join(toc_blobs)
    if not blob:
        raise SystemExit(f"PyInstaller did not write analysis files under {engine_dir}")

    missing: list[str] = []
    for module in REQUIRED_PYZ_MODULES:
        as_py = module.replace(".", "/") + ".py"
        if (
            f"'{module}'" not in blob
            and f'"{module}"' not in blob
            and as_py not in blob
            and module.replace(".", os.sep) + ".py" not in blob
        ):
            missing.append(module)
    if missing:
        raise SystemExit(
            "PyInstaller bundle is missing required engine modules: "
            + ", ".join(missing)
        )
    print("Verified first-party modules in sidecar bundle.", flush=True)


def sidecar_name(target: str) -> str:
    ext = ".exe" if sys.platform == "win32" or target.endswith("windows-msvc") else ""
    return f"inbound-engine-{target}{ext}"


def go2rtc_sidecar_name(target: str) -> str:
    ext = ".exe" if sys.platform == "win32" or target.endswith("windows-msvc") else ""
    return f"go2rtc-{target}{ext}"


def ensure_go2rtc() -> Path:
    """Download go2rtc into edge/bin/ so PyInstaller and Tauri can bundle it."""
    sys.path.insert(0, str(EDGE))
    from media.go2rtc import binary_filename, ensure_binary

    dest = EDGE / "bin" / binary_filename()
    path = ensure_binary(dest)
    print(f"go2rtc binary: {path}", flush=True)
    return path


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

    go2rtc_path = ensure_go2rtc()

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

    _assert_modules_bundled(work)

    built = dist / exe_name()
    if not built.exists():
        raise SystemExit(f"PyInstaller did not produce {built}")

    target = args.target.strip() or detect_target_triple()
    BINARIES.mkdir(parents=True, exist_ok=True)
    dest = BINARIES / sidecar_name(target)
    shutil.copy2(built, dest)
    dest.chmod(dest.stat().st_mode | 0o111)
    print(f"Sidecar installed: {dest}", flush=True)

    go2rtc_dest = BINARIES / go2rtc_sidecar_name(target)
    shutil.copy2(go2rtc_path, go2rtc_dest)
    go2rtc_dest.chmod(go2rtc_dest.stat().st_mode | 0o111)
    print(f"go2rtc installed: {go2rtc_dest}", flush=True)


if __name__ == "__main__":
    main()
