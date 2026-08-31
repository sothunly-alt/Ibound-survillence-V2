# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Inbound Surveillance sidecar binary.

The frozen executable is named ``inbound-engine``. ``build_sidecar.py``
renames it with the Tauri target triple and copies it to
``src-tauri/binaries/``.
"""

from __future__ import annotations

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
    "proof",
    "report",
    "roi_edit",
    "telegram_out",
]

for name in (
    "yolo11n-pose.pt",
    "config.example.yaml",
    "hub.html",
):
    src = EDGE / name
    if src.exists():
        datas.append((str(src), "."))

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
