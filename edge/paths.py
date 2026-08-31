"""Resolve bundled assets vs writable data for source and PyInstaller runs."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _meipass() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return None


def resource_dir() -> Path:
    frozen = _meipass()
    if frozen is not None:
        return frozen
    return Path(__file__).resolve().parent


def get_resource_path(relative_path: str) -> Path:
    """Locate a bundled file (model, hub.html, example config).

    PyInstaller extracts datas into ``sys._MEIPASS``. Source checkouts
    resolve relative to this module (the ``edge/`` directory).
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative_path
    return Path(__file__).resolve().parent / relative_path


def data_dir() -> Path:
    """Writable config, SQLite, and proof stills.

    Frozen binaries cannot persist files inside the extract dir, so the
    platform application-data folder is used. Source checkouts keep files
    next to the Python modules. Override with ``INBOUND_DATA_DIR``.
    """
    override = os.environ.get("INBOUND_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    if getattr(sys, "frozen", False) or _meipass() is not None:
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
            path = base / "Inbound Surveillance"
        elif sys.platform == "darwin":
            path = Path.home() / "Library" / "Application Support" / "Inbound Surveillance"
        else:
            xdg = os.environ.get("XDG_DATA_HOME")
            base = Path(xdg) if xdg else Path.home() / ".local" / "share"
            path = base / "inbound-surveillance"
        path.mkdir(parents=True, exist_ok=True)
        return path

    path = Path(__file__).resolve().parent
    path.mkdir(parents=True, exist_ok=True)
    return path
