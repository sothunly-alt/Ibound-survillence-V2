# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Inbound Surveillance sidecar binary.

The frozen executable is named ``inbound-engine``. ``build_sidecar.py``
renames it with the Tauri target triple and copies it to
``src-tauri/binaries/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.building.api import EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_all, collect_submodules

EDGE = Path(SPECPATH).resolve()

datas: list[tuple[str, str]] = []
binaries: list = []
hiddenimports = [
    "ultralytics",
    "torch",
    "torchvision",
    "cv2",
    "yaml",
    "requests",
    "requests.auth",
    "sqlite3",
    "PIL",
    "paths",
    "db",
    "occupancy",
    "person",
    "face_id",
    "tracker",
    "reid",
    "runtime",
    "proof",
    "report",
    "roi_edit",
    "telegram_out",
    "capture",
    "adapters",
    "adapters.base",
    "adapters.webcam",
    "adapters.rtsp",
    "adapters.phone_http",
    "adapters.gateway",
    "adapters.onvif",
    "adapters.tapo",
    "adapters.webrtc",
    "discovery",
    "discovery.scanner",
    "zeroconf",
    "media",
    "media.client",
    "media.go2rtc",
    "sensors",
    "sensors.wifi_tracker",
    "ai_auditor",
    "telegram_link",
    "service_patterns",
    "vehicle",
    "bay_zoom",
    "liveness",
    "corroborate",
    "negatives",
]

for name in (
    "yolo11n_improved.pt",
    "yolo11n-pose.pt",
    "yolo11n.pt",
    "config.example.yaml",
    "hub.html",
    "inb_surveillance.png",
    "inb_surveillance-removebg-preview.png",
):
    src = EDGE / name
    if src.exists():
        datas.append((str(src), "."))
    repo_src = EDGE.parent / name
    if repo_src.exists() and (str(repo_src), ".") not in datas:
        datas.append((str(repo_src), "."))

REPO = EDGE.parent
for env_candidate in (EDGE / ".env", REPO / ".env"):
    if env_candidate.is_file():
        datas.append((str(env_candidate), "."))
        break

faces = EDGE / "faces"
if faces.is_dir():
    datas.append((str(faces), "faces"))
static_dir = EDGE / "static"
if static_dir.is_dir():
    datas.append((str(static_dir), "static"))
models = EDGE / "models"
if models.is_dir():
    for onnx in models.glob("*.onnx"):
        datas.append((str(onnx), "models"))

go2rtc_name = "go2rtc.exe" if sys.platform == "win32" else "go2rtc"
go2rtc_bin = EDGE / "bin" / go2rtc_name
if go2rtc_bin.is_file():
    binaries.append((str(go2rtc_bin), "bin"))

for pkg in ("ultralytics", "torch", "torchvision", "cv2", "PIL", "yaml", "requests"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    except Exception:
        try:
            pkg_hidden = collect_submodules(pkg)
        except Exception:
            pkg_hidden = []
        pkg_datas, pkg_binaries = [], []
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [str(EDGE / "launcher.py")],
    pathex=[str(EDGE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib.tests", "pytest", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="inbound-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
