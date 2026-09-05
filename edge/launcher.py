"""Inbound Garage — live AI auto-shop operations hub.

Serves the garage command-center dashboard (edge/hub.html):
- Live view with YOLO11 pose overlay and multi-bay service ROIs.
- Face ID attendance, wrench-time classification, Wi-Fi presence.
- End-of-day scorecards for Telegram and the owner dashboard.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from paths import data_dir, get_resource_path, resource_dir

# Default FFmpeg RTSP options for any leftover OpenCV opens (CLI preview).
# The grabber's RTSPAdapter overrides per-attempt with a 2s stimeout.
# Set before importing cv2 / adapters so FFmpeg picks the timeout up.
_RTSP_STIMEOUT_US = 2_000_000
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    f"rtsp_transport;tcp|stimeout;{_RTSP_STIMEOUT_US}|max_delay;500000",
)
if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import cv2
import numpy as np
import requests
import yaml

import re

ROOT = resource_dir()
DATA_DIR = data_dir()
VIDEOS_DIR = ROOT / "videos"


def init_videos_dir() -> Path:
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    sample = ROOT.parent / "tools" / "virtual-camera" / "videos" / "sample_garage_demo.mp4"
    target = VIDEOS_DIR / "sample_garage_demo.mp4"
    if not target.exists() and sample.is_file():
        try:
            target.symlink_to(sample.resolve())
        except Exception:
            try:
                import shutil
                shutil.copy2(sample, target)
            except Exception:
                pass
    return VIDEOS_DIR


init_videos_dir()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import create_adapter, ingest_kind
from adapters.base import (
    BaseCameraAdapter,
    FramePacket,
    parse_source,
    protocol_from_source,
    redact_source,
    unwrap_local_video_source,
)
from adapters.onvif import onvif_xaddr
from adapters.webcam import open_webcam_index as _open_webcam_index
from capture import AsyncFrameGrabber, request_still
from discovery import DiscoveryEngine
from media.go2rtc import Go2RtcManager, sanitize_stream_id
from db import (
    add_wifi_minutes,
    close_bay_sessions,
    close_empty_bays,
    complete_vehicle_job,
    connect,
    get_daily_garage_summary,
    get_or_create_vehicle_job,
    get_vehicle_job_history,
    has_opened_today,
    insert_event,
    list_vehicle_jobs,
    record_face_clock_in,
    record_face_clock_out,
    update_technician_activity,
    update_vehicle_job_activity,
    upsert_minute,
)
from face_id import (
    create_identity,
    delete_identity,
    delete_identity_photo,
    get_identity,
    identity_photo_path,
    list_identities,
    save_identity_photo,
    till_status_label,
    try_create_face_recognizer,
)
from occupancy import (
    DEFAULT_BAYS,
    BayZoneManager,
    GhostCounter,
    GhostState,
    detection_in_bay,
    normalize_bays,
    roi_to_pixels,
)
from person import Detection, draw_detection, person_detections
from proof import save_proof, scale_roi_px
from reid import try_create_body_reid
from report import build_report
from runtime import resolve_runtime, resolve_weights_file
from sensors.wifi_tracker import WifiTracker, normalize_wifi_devices, presence_status
from service_patterns import KNOWLEDGE_BASE, evaluate_completed_vehicle_job
from telegram_out import TelegramOut
from tracker import PersonTracker, run_identity_pipeline
from vehicle import VehicleDetection, extract_vehicle_detections


def find_free_port(default_port: int = 8765) -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", default_port))
            return default_port
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def get_config_path() -> Path:
    real = DATA_DIR / "config.yaml"
    if real.exists():
        return real
    example = get_resource_path("config.example.yaml")
    if example.exists():
        return example
    return real


def read_config() -> dict[str, Any]:
    path = get_config_path()
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    token = os.environ.get("TELEGRAM_BOT_TOKEN", data.get("telegram_bot_token") or "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", data.get("telegram_chat_id") or "")
    data["telegram_bot_token"] = token
    data["telegram_chat_id"] = chat
    data["cameras"] = _normalize_cameras(data.get("cameras"))
    data["active_camera_id"] = str(data.get("active_camera_id") or "")
    data["garage_name"] = str(data.get("garage_name") or data.get("venue") or "Demo Garage")
    data["venue"] = str(data.get("venue") or data["garage_name"])
    data["close_time"] = str(data.get("close_time") or "18:00")
    active_cid = data["active_camera_id"]
    active_cam = next((c for c in data["cameras"] if c["id"] == active_cid), None)
    if active_cam and active_cam.get("bays"):
        data["bays"] = active_cam["bays"]
    else:
        fallback_roi = parse_roi(active_cam.get("roi")) if active_cam else parse_roi(data.get("roi"))
        data["bays"] = normalize_bays(data.get("bays"), fallback_roi=fallback_roi)
        if active_cam and not active_cam.get("bays"):
            active_cam["bays"] = data["bays"]
    data["wifi_devices"] = normalize_wifi_devices(data.get("wifi_devices"))
    hours = data.get("operating_hours")
    if not isinstance(hours, dict):
        data["operating_hours"] = {
            "open": str(data.get("open_time") or "08:00"),
            "close": str(data.get("close_time") or "18:00"),
        }
    else:
        data["operating_hours"] = {
            "open": str(hours.get("open") or data.get("open_time") or "08:00"),
            "close": str(hours.get("close") or data.get("close_time") or "18:00"),
        }
        data["open_time"] = data["operating_hours"]["open"]
        data["close_time"] = data["operating_hours"]["close"]
    return data


_SETTINGS_KEYS = (
    "telegram_bot_token",
    "telegram_chat_id",
    "venue",
    "garage_name",
    "open_time",
    "close_time",
    "absent_seconds",
)


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    target = DATA_DIR / "config.yaml"
    current = {}
    if target.exists():
        try:
            with target.open("r", encoding="utf-8") as f:
                current = yaml.safe_load(f) or {}
        except Exception:
            current = {}
    else:
        example = get_resource_path("config.example.yaml")
        if example.exists():
            try:
                with example.open("r", encoding="utf-8") as f:
                    current = yaml.safe_load(f) or {}
            except Exception:
                current = {}

    current.update(updates)
    if "cameras" in current:
        current["cameras"] = _normalize_cameras(current.get("cameras"))
    with target.open("w", encoding="utf-8") as f:
        yaml.safe_dump(current, f, sort_keys=False)
    return current


def _normalize_xaddrs(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _normalize_cameras(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        source = item.get("source", "")
        if not isinstance(source, int):
            source = str(source or "")
        main_source = item.get("main_source", "")
        if not isinstance(main_source, int):
            main_source = str(main_source or "")
        cam_roi = parse_roi(item.get("roi")) or [0.30, 0.20, 0.40, 0.60]
        raw_bays = item.get("bays")
        if raw_bays and isinstance(raw_bays, list):
            cam_bays = parse_bays(raw_bays, fallback_roi=cam_roi, seed_if_empty=False)
        else:
            cam_bays = []
        out.append(
            {
                "id": cid,
                "name": name or "Untitled camera",
                "source": source,
                "main_source": main_source,
                "protocol": str(item.get("protocol") or protocol_from_source(source)),
                "vendor": str(item.get("vendor") or "generic"),
                "username": str(item.get("username") or ""),
                "xaddrs": _normalize_xaddrs(item.get("xaddrs")),
                "rotate": item.get("rotate", "auto") if str(item.get("rotate", "")).lower() == "auto" else item.get("rotate", 0),
                "flip": str(item.get("flip") or "none"),
                "roi": cam_roi,
                "bays": cam_bays,
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return out


def upsert_camera(cfg: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    cameras = _normalize_cameras(cfg.get("cameras"))
    cid = str(fields.get("id") or "").strip() or f"cam-{int(time.time() * 1000)}"
    name = str(fields.get("name") or "").strip() or "Untitled camera"
    existing: dict[str, Any] = {}
    for cam in cameras:
        if cam["id"] == cid:
            existing = cam
            break
    source = fields.get("source", existing.get("source", cfg.get("source", "")))
    if not isinstance(source, int):
        source = str(source or "")
    if not str(source).strip() and fields.get("xaddrs"):
        xaddrs_fallback = _normalize_xaddrs(fields.get("xaddrs"))
        if xaddrs_fallback:
            source = xaddrs_fallback[0]
    if "main_source" in fields:
        main_source = fields.get("main_source", "")
    else:
        main_source = existing.get("main_source", "")
    if not isinstance(main_source, int):
        main_source = str(main_source or "")
    if "xaddrs" in fields:
        xaddrs = _normalize_xaddrs(fields.get("xaddrs"))
    else:
        xaddrs = _normalize_xaddrs(existing.get("xaddrs"))
    roi = parse_roi(fields.get("roi"))
    if roi is None and existing:
        roi = parse_roi(existing.get("roi"))
    if roi is None:
        roi = [0.30, 0.20, 0.40, 0.60]
    rotate = fields.get("rotate", existing.get("rotate", cfg.get("rotate", "auto")))
    bays_val = fields.get("bays") if "bays" in fields else existing.get("bays")
    if bays_val and isinstance(bays_val, list):
        bays = parse_bays(bays_val, fallback_roi=roi, seed_if_empty=False)
    else:
        bays = list(existing.get("bays") or [])
    entry = {
        "id": cid,
        "name": name,
        "source": source,
        "main_source": main_source,
        "protocol": str(fields.get("protocol") or existing.get("protocol") or protocol_from_source(source)),
        "vendor": str(fields.get("vendor") or existing.get("vendor") or "generic"),
        "username": str(fields.get("username") if "username" in fields else existing.get("username") or ""),
        "xaddrs": xaddrs,
        "rotate": rotate,
        "flip": str(fields.get("flip") or existing.get("flip") or cfg.get("flip") or "none"),
        "roi": roi,
        "bays": bays,
        "enabled": bool(fields.get("enabled", existing.get("enabled", True))),
    }
    for i, cam in enumerate(cameras):
        if cam["id"] == cid:
            cameras[i] = entry
            break
    else:
        cameras.append(entry)
    cfg["cameras"] = cameras
    cfg["active_camera_id"] = cid
    cfg["roi"] = roi
    if bays:
        cfg["bays"] = bays
    return entry


def parse_rotate(value: Any) -> int:
    if value is None or str(value).strip().lower() in ("", "auto"):
        return 0
    try:
        deg = int(value)
        deg = ((deg % 360) + 360) % 360
        return deg if deg in (0, 90, 180, 270) else 0
    except (TypeError, ValueError):
        return 0


def is_auto_rotate(value: Any) -> bool:
    return value is None or str(value).strip().lower() in ("", "auto")


def suggest_rotate(source: Any, frame) -> int:
    """Return default rotation for auto-orient.

    Default to 0 (native camera orientation) so streams are not unexpectedly
    rotated sideways. Users can select CW (90°), CCW (270°), or 180° for
    mounted phone orientations.
    """
    return 0


def resolve_orient(
    rotate_value: Any, source: Any = None, frame=None, flip_value: Any = "none"
) -> tuple[int, str]:
    if is_auto_rotate(rotate_value):
        return suggest_rotate(source, frame), "none"
    return parse_rotate(rotate_value), parse_flip(flip_value)


def parse_flip(value: Any) -> str:
    text = str(value or "none").strip().lower()
    if text in ("h", "horizontal", "x"):
        return "h"
    if text in ("v", "vertical", "y"):
        return "v"
    return "none"


def orient_frame(frame, rotate_deg: int, flip: str):
    if rotate_deg == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rotate_deg == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif rotate_deg == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

    if flip == "h":
        frame = cv2.flip(frame, 1)
    elif flip == "v":
        frame = cv2.flip(frame, 0)
    return frame



def parse_roi(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    w = max(0.02, min(1.0, w))
    h = max(0.02, min(1.0, h))
    x = max(0.0, min(1.0 - w, x))
    y = max(0.0, min(1.0 - h, y))
    return [x, y, w, h]


def parse_bays(
    value: Any,
    fallback_roi: list[float] | None = None,
    *,
    seed_if_empty: bool = True,
) -> list[dict[str, Any]]:
    return normalize_bays(value, fallback_roi=fallback_roi, seed_if_empty=seed_if_empty)


def parse_clock(value: Any, default: str = "08:00") -> str:
    text = str(value or default).strip()
    parts = text.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return default
        return f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError, IndexError):
        return default


def shop_is_open(cfg: dict[str, Any], now: datetime | None = None) -> bool:
    stamp = now or datetime.now()
    hours = cfg.get("operating_hours") if isinstance(cfg.get("operating_hours"), dict) else {}
    open_s = parse_clock(hours.get("open") or cfg.get("open_time"), "08:00")
    close_s = parse_clock(hours.get("close") or cfg.get("close_time"), "18:00")
    current = stamp.strftime("%H:%M")
    if open_s <= close_s:
        return open_s <= current < close_s
    return current >= open_s or current < close_s


def garage_name_of(cfg: dict[str, Any]) -> str:
    return str(cfg.get("garage_name") or cfg.get("venue") or "Demo Garage")


def _needs_onvif_onboard(protocol: Any, source: Any, xaddrs: Any) -> bool:
    if str(protocol or "").strip().lower() != "onvif":
        return False
    text = str(source or "").strip().lower()
    if text.startswith("rtsp://"):
        return False
    if _normalize_xaddrs(xaddrs) or "onvif" in text or text.startswith("http://") or text.startswith("https://"):
        return True
    return False


class _ConnectErrorAdapter(BaseCameraAdapter):
    """Queued after a failed onboard so telemetry becomes FAILED without crashing."""

    def __init__(self, message: str):
        self.error: str | None = message

    def connect(self) -> bool:
        return False

    def read_frame(self) -> FramePacket | None:
        return None

    def release(self) -> None:
        return None

    def is_connected(self) -> bool:
        return False


class CameraStreamWorker:
    """Maintains a background grabber and latest-frame cache for a single camera.

    Keeps the camera stream alive across layout switches and active-camera changes,
    enabling all cameras in the multi-camera grid to stream simultaneously.
    """

    def __init__(self, camera_id: str, camera_cfg: dict[str, Any], gateway: Any = None):
        self.camera_id = str(camera_id)
        self.cfg = dict(camera_cfg)
        self.gateway = gateway
        self.grabber = AsyncFrameGrabber()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.latest_jpeg: bytes | None = None
        self.latest_ts: float = 0.0
        self.latest_packet: FramePacket | None = None
        self.is_active_ai: bool = False

    def update_cfg(self, new_cfg: dict[str, Any]) -> bool:
        """Update config. Reconnects adapter if source, credentials, or protocol changed."""
        with self._lock:
            old_src = self.cfg.get("source")
            old_proto = self.cfg.get("protocol")
            old_user = self.cfg.get("username")
            old_pass = self.cfg.get("password")
            old_main = self.cfg.get("main_source")
            self.cfg.update(new_cfg)
            need_reconnect = (
                old_src != self.cfg.get("source")
                or old_proto != self.cfg.get("protocol")
                or old_user != self.cfg.get("username")
                or old_pass != self.cfg.get("password")
                or old_main != self.cfg.get("main_source")
            )
        if need_reconnect and self._thread and self._thread.is_alive():
            self._reconnect()
            return True
        return False

    def _build_adapter(self) -> BaseCameraAdapter:
        src = self.cfg.get("source")
        proto = self.cfg.get("protocol")
        user = str(self.cfg.get("username") or "")
        pwd = str(self.cfg.get("password") or "")
        xaddrs = self.cfg.get("xaddrs") or []
        main_src = str(self.cfg.get("main_source") or "")
        stream_id = sanitize_stream_id(self.camera_id)
        return create_adapter(
            src,
            gateway=self.gateway if stream_id else None,
            stream_id=stream_id,
            protocol=proto,
            username=user,
            password=pwd,
            xaddrs=xaddrs,
            main_source=main_src,
        )

    def _reconnect(self) -> None:
        try:
            adapter = self._build_adapter()
            self.grabber.switch_source(adapter)
        except Exception as exc:
            print(f"[CameraStreamWorker:{self.camera_id}] Reconnect error: {exc}", flush=True)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.grabber.start()
        self._reconnect()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"CamWorker-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.grabber.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None

    def _loop(self) -> None:
        last_encode_time = 0.0
        last_reconnect_time = time.time()
        while not self._stop_event.is_set():
            now = time.time()
            if not self.is_active_ai:
                # If grabber failed or lost adapter, attempt periodic reconnection
                if (
                    self.grabber.connection_state in ("FAILED", "STANDBY")
                    or getattr(self.grabber, "_adapter", None) is None
                ) and (now - last_reconnect_time >= 3.0):
                    last_reconnect_time = now
                    self._reconnect()

                packet = self.grabber.get_latest_frame(timeout=0.15)
                if packet is not None and packet.frame is not None:
                    if (now - last_encode_time) >= 0.09:
                        last_encode_time = now
                        frame = packet.frame
                        rot = int(self.cfg.get("rotate") or 0)
                        flp = str(self.cfg.get("flip") or "none")
                        if rot != 0 or flp != "none":
                            frame = orient_frame(frame, rot, flp)
                        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
                        if ok:
                            data = buf.tobytes()
                            with self._lock:
                                self.latest_jpeg = data
                                self.latest_ts = now
                                self.latest_packet = packet
                else:
                    time.sleep(0.04)
            else:
                time.sleep(0.06)


class CameraStreamPool:
    """Manages concurrent CameraStreamWorkers for all configured cameras."""

    def __init__(self, gateway: Any = None):
        self.gateway = gateway
        self._workers: dict[str, CameraStreamWorker] = {}
        self._lock = threading.Lock()
        self.active_camera_id: str = ""

    def set_active_camera(self, camera_id: str | None) -> None:
        cid = str(camera_id or "").strip()
        with self._lock:
            self.active_camera_id = cid
            for wid, worker in self._workers.items():
                worker.is_active_ai = (wid == cid)

    def get_worker(self, camera_id: str | None) -> CameraStreamWorker | None:
        cid = str(camera_id or "").strip()
        with self._lock:
            return self._workers.get(cid)

    def get_latest_jpeg(self, camera_id: str | None) -> tuple[bytes | None, str]:
        worker = self.get_worker(camera_id)
        if worker and worker.latest_jpeg:
            return worker.latest_jpeg, "image/jpeg"
        return None, "image/jpeg"

    def sync_cameras(self, cameras: list[dict[str, Any]]) -> None:
        active_id = self.active_camera_id
        configured_ids = set()
        with self._lock:
            for cam in cameras:
                cid = str(cam.get("id") or "").strip()
                src = cam.get("source")
                if not cam.get("enabled", True):
                    continue
                if not cid or src is None or str(src).strip() == "":
                    continue
                configured_ids.add(cid)
                if cid not in self._workers:
                    worker = CameraStreamWorker(cid, cam, gateway=self.gateway)
                    worker.is_active_ai = (cid == active_id)
                    self._workers[cid] = worker
                    worker.start()
                else:
                    self._workers[cid].update_cfg(cam)
                    self._workers[cid].is_active_ai = (cid == active_id)

            to_remove = [wid for wid in self._workers if wid not in configured_ids]
            for wid in to_remove:
                worker = self._workers.pop(wid)
                worker.stop()

    def stop(self) -> None:
        with self._lock:
            for worker in self._workers.values():
                worker.stop()
            self._workers.clear()


class LiveStreamEngine:
    """Background engine that captures video, performs YOLO11 pose inference,

    tracks garage bay occupancy and wrench time, saves proofs, and delivers
    frames to the web UI. Media ingest (AsyncFrameGrabber + go2rtc) is unchanged.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.is_streaming = False
        self.thread: threading.Thread | None = None
        self.current_frame_jpeg: bytes | None = None
        self.mjpeg_generation = 0
        self.frame_seq = 0
        self.new_frame_event = threading.Event()
        self.media = Go2RtcManager()
        self.camera_pool = CameraStreamPool(gateway=self.media)
        self._fallback_grabber = AsyncFrameGrabber()
        self.discovery = DiscoveryEngine()
        self._gateway_stream_id: str | None = None
        self._gateway_main_stream_id: str | None = None
        self.model = None
        self.face_rec = None
        self.tracker: PersonTracker | None = None
        self.reid = None
        self.runtime_profile = None
        self.staff_names: list[str] = []
        self.identities: list[str] = []

        # Telemetry stats
        self.fps = 0.0
        self.infer_ms = 0.0
        self.is_occupied = False
        self.empty_elapsed = 0.0
        self.person_count = 0
        self.status_text = "STANDBY"
        self.connection_state = "STANDBY"
        self.stream_resolution = "--"
        self.error_message: str | None = None
        self.bay_telemetry: list[dict[str, Any]] = []
        self.last_face_seen: dict[str, float] = {}
        self._wifi_last_tick: float | None = None
        self._camera_frame_cache: dict[str, tuple[bytes, str, float]] = {}

        # Load initial config. SQLite is opened on the worker thread —
        # connections cannot be shared across threads.
        self.cfg = read_config()
        self.conn = None
        self.bot = TelegramOut(self.cfg.get("telegram_bot_token", ""), self.cfg.get("telegram_chat_id", ""))
        self.bay_manager = BayZoneManager(
            self.cfg.get("bays"),
            fallback_roi=parse_roi(self.cfg.get("roi")),
        )
        self.wifi = WifiTracker(self.cfg.get("wifi_devices"))
        self.bay_telemetry = self.bay_manager.telemetry()

    @property
    def grabber(self) -> AsyncFrameGrabber:
        worker = self.camera_pool.get_worker(self.cfg.get("active_camera_id"))
        if worker is not None:
            return worker.grabber
        return self._fallback_grabber

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            try:
                self.media.start()
                self.register_all_cameras_in_gateway()
            except Exception as exc:
                print(f"[go2rtc] start failed: {exc}", flush=True)
            self._fallback_grabber.start()
            self.camera_pool.set_active_camera(self.cfg.get("active_camera_id") or "")
            self.camera_pool.sync_cameras(list(self.cfg.get("cameras") or []))
            self.wifi.start()
            self.thread = threading.Thread(target=self._worker_loop, daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            self.is_streaming = False
            self._drop_gateway_stream()
        self._fallback_grabber.stop()
        self.camera_pool.stop()
        try:
            self.wifi.stop()
        except Exception:
            pass
        try:
            self.media.stop()
        except Exception:
            pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def reload_face_id(self) -> None:
        if self.face_rec is None:
            return
        try:
            self.face_rec.reload_enrolled_faces()
        except Exception as exc:
            print(f"[FaceID] Reload failed: {exc}")

    def _drop_gateway_stream(self) -> None:
        sid = self._gateway_stream_id
        main_sid = self._gateway_main_stream_id
        self._gateway_stream_id = None
        self._gateway_main_stream_id = None
        for name in (sid, main_sid):
            if not name:
                continue
            try:
                self.media.client.remove_stream(name)
            except Exception:
                pass

    def register_all_cameras_in_gateway(self) -> None:
        """Register all configured network cameras and video files into go2rtc so multi-camera grid can stream simultaneously."""
        if not self.media.is_ready():
            return
        cameras = list(self.cfg.get("cameras") or [])
        for cam in cameras:
            cid = cam.get("id")
            src = cam.get("source")
            if not cid or not src:
                continue
            kind = ingest_kind(src, cam.get("protocol"))
            if kind == "webcam":
                continue
            stream_id = sanitize_stream_id(cid)
            if kind == "video":
                try:
                    from adapters.video_file import resolve_video_path
                    vp = resolve_video_path(src)
                    if vp.is_file():
                        url = f"ffmpeg:{vp.resolve()}#video=h264#loop"
                    else:
                        continue
                except Exception:
                    continue
            else:
                url = str(parse_source(src))
            try:
                self.media.client.register_stream(stream_id, url)
                main_src = str(cam.get("main_source") or "").strip()
                if main_src:
                    self.media.client.register_stream(f"{stream_id}-main", main_src)
            except Exception as exc:
                print(f"[go2rtc] background register failed for {stream_id}: {exc}", flush=True)

    def _bind_gateway(self, source: Any, camera_id: str, main_source: Any = None) -> str | None:
        """Register a network camera with go2rtc. Webcams skip the gateway."""
        kind = ingest_kind(source, None)
        if kind in ("webcam", "video") or not self.media.is_ready():
            return None
        stream_id = sanitize_stream_id(camera_id or "live")
        url = str(parse_source(source))
        try:
            ok = self.media.client.register_stream(stream_id, url)
        except Exception as exc:
            print(f"[go2rtc] register failed: {exc}", flush=True)
            ok = False
        if not ok:
            print(f"[go2rtc] could not register {stream_id}", flush=True)
            return None
        self._gateway_stream_id = stream_id
        main_url = str(main_source or "").strip()
        main_id = f"{stream_id}-main"
        if main_url:
            try:
                if self.media.client.register_stream(main_id, main_url):
                    self._gateway_main_stream_id = main_id
                    print(f"[go2rtc] stream {main_id} <- {redact_source(main_url)}", flush=True)
                else:
                    self._gateway_main_stream_id = None
            except Exception as exc:
                print(f"[go2rtc] main register failed: {exc}", flush=True)
                self._gateway_main_stream_id = None
        else:
            self._gateway_main_stream_id = None
        print(f"[go2rtc] stream {stream_id} <- {redact_source(url)}", flush=True)
        return stream_id

    def connect_camera(self, new_cfg: dict[str, Any]) -> dict[str, Any]:
        """Queue a camera switch on the grabber thread and return immediately.

        Never opens VideoCapture on the HTTP thread — V4L2/DirectShow handles
        are exclusive and OpenCV objects are thread-affine. Status moves
        CONNECTING → CONNECTED or FAILED via telemetry.
        """
        payload = dict(new_cfg)
        cid = str(payload.pop("camera_id", "") or "")
        cam_name = str(payload.pop("camera_name", "") or "")
        if cid:
            payload.pop("id", None)
        else:
            cid = str(payload.pop("id", "") or "")
        if cam_name:
            payload.pop("name", None)
        else:
            cam_name = str(payload.pop("name", "") or "")
        creds = payload.pop("credentials", None)
        if not isinstance(creds, dict):
            creds = {}
        password = str(payload.pop("password", "") or creds.get("password") or "")
        camera_fields = {
            "id": cid,
            "name": cam_name,
            "protocol": payload.pop("protocol", ""),
            "vendor": payload.pop("vendor", ""),
            "username": str(payload.pop("username", "") or creds.get("username") or ""),
        }
        if "roi" in payload:
            camera_fields["roi"] = payload.pop("roi")
        if "main_source" in payload:
            camera_fields["main_source"] = payload.pop("main_source")
        if "xaddrs" in payload:
            camera_fields["xaddrs"] = payload.pop("xaddrs")
        with self.lock:
            self.cfg.update(payload)
            recovered = unwrap_local_video_source(self.cfg.get("source"))
            proto_hint = str(camera_fields.get("protocol") or self.cfg.get("protocol") or "").lower()
            if recovered or proto_hint in ("video", "file"):
                if recovered:
                    self.cfg["source"] = recovered
                self.cfg["protocol"] = "video"
                camera_fields["protocol"] = "video"
            if str(camera_fields.get("name") or "").strip() or str(camera_fields.get("id") or "").strip():
                camera_fields["source"] = self.cfg.get("source")
                camera_fields["rotate"] = self.cfg.get("rotate")
                camera_fields["flip"] = self.cfg.get("flip")
                upsert_camera(self.cfg, camera_fields)
            cameras = list(self.cfg.get("cameras") or [])
            active_id = str(cid or self.cfg.get("active_camera_id") or "")
            if active_id:
                self.cfg["active_camera_id"] = active_id
            active: dict[str, Any] = {}
            for cam in cameras:
                if cam.get("id") == active_id:
                    active = cam
                    break
            protocol = str(
                active.get("protocol")
                or camera_fields.get("protocol")
                or self.cfg.get("protocol")
                or protocol_from_source(self.cfg.get("source"))
            )
            main_source = active.get("main_source") or self.cfg.get("main_source") or ""
            xaddrs = active.get("xaddrs") or camera_fields.get("xaddrs") or []
            username = str(active.get("username") or camera_fields.get("username") or "")
            self.cfg["protocol"] = protocol
            self.cfg["main_source"] = main_source
            cfg_to_save = dict(self.cfg)
            self.bot = TelegramOut(
                self.cfg.get("telegram_bot_token", ""),
                self.cfg.get("telegram_chat_id", ""),
            )
            self.error_message = None
            self.is_streaming = True
            self.current_frame_jpeg = None
            self.mjpeg_generation += 1
            self.fps = 0.0
            self.is_occupied = False
            self.person_count = 0
            self.staff_names = []
            self.identities = []
            self.status_text = "CONNECTING"
            self.connection_state = "CONNECTING"
            self.new_frame_event.set()
            source = self.cfg.get("source", 0)
            active_bays = list(active.get("bays") or [])
            if not active_bays:
                active_roi = parse_roi(active.get("roi")) or parse_roi(self.cfg.get("roi")) or [0.30, 0.20, 0.40, 0.60]
                active_bays = parse_bays(None, fallback_roi=active_roi, seed_if_empty=True)
                active["bays"] = active_bays
            self.cfg["bays"] = active_bays
            roi = list(parse_roi(active.get("roi")) or self.cfg.get("roi") or (active_bays[0]["roi"] if active_bays else [0.30, 0.20, 0.40, 0.60]))
            self.cfg["roi"] = roi
            self.bay_manager.set_bays(active_bays)
            self.bay_telemetry = self.bay_manager.telemetry()
            bays = list(self.cfg.get("bays") or DEFAULT_BAYS)
            rotate = self.cfg.get("rotate", 0)
            flip = self.cfg.get("flip", "none")
            cfg_to_save = dict(self.cfg)

        # Perform disk write and network/gateway operations outside self.lock to avoid deadlock
        save_config(cfg_to_save)
        try:
            self.register_all_cameras_in_gateway()
        except Exception:
            pass

        if _needs_onvif_onboard(protocol, source, xaddrs):
            self.grabber.mark_connecting()
            threading.Thread(
                target=self._onboard_onvif,
                kwargs={
                    "xaddrs": xaddrs,
                    "source": source,
                    "username": username,
                    "password": password,
                    "camera_id": active_id or cid,
                    "camera_name": cam_name or str(active.get("name") or ""),
                },
                name="OnvifOnboard",
                daemon=True,
            ).start()
            media = {"ready": self.media.is_ready()}
            return {
                "success": True,
                "pending": True,
                "connection": "CONNECTING",
                "status": "CONNECTING",
                "message": f"Resolving ONVIF '{redact_source(onvif_xaddr(source, xaddrs))}'…",
                "roi": roi,
                "bays": bays,
                "rotate": rotate,
                "flip": flip,
                "cameras": cameras,
                "active_camera_id": active_id,
                "stream_id": None,
                "media": media,
            }

        stream_id = self._bind_gateway(source, active_id, main_source=main_source)
        self.camera_pool.set_active_camera(active_id)
        if self.running:
            self.camera_pool.sync_cameras(cameras)
            worker = self.camera_pool.get_worker(active_id)
            if worker is None:
                adapter = create_adapter(
                    source,
                    gateway=self.media if stream_id else None,
                    stream_id=stream_id,
                    protocol=protocol,
                    username=username,
                    password=password,
                    xaddrs=xaddrs,
                    main_source=main_source,
                )
                self.grabber.switch_source(adapter)
        else:
            adapter = create_adapter(
                source,
                gateway=self.media if stream_id else None,
                stream_id=stream_id,
                protocol=protocol,
                username=username,
                password=password,
                xaddrs=xaddrs,
                main_source=main_source,
            )
            self.grabber.switch_source(adapter)
        media = self.media.status(stream_id) if stream_id else {"ready": self.media.is_ready()}
        return {
            "success": True,
            "pending": True,
            "connection": "CONNECTING",
            "status": "CONNECTING",
            "message": f"Connecting to '{redact_source(source)}'…",
            "roi": roi,
            "bays": bays,
            "rotate": rotate,
            "flip": flip,
            "cameras": cameras,
            "active_camera_id": active_id,
            "stream_id": stream_id,
            "media": media,
        }

    def _onboard_onvif(
        self,
        *,
        xaddrs: Any,
        source: Any,
        username: str,
        password: str,
        camera_id: str,
        camera_name: str,
    ) -> None:
        """SOAP on a worker: persist RTSP URLs, then queue the grabber switch."""
        from adapters.onvif import ONVIFAdapter

        adapter = ONVIFAdapter(
            onvif_xaddr(source, xaddrs),
            username=username,
            password=password,
            xaddrs=xaddrs,
            source=source,
        )
        try:
            ok = adapter.resolve()
        except Exception as exc:
            ok = False
            adapter.error = str(exc)
        if not ok:
            self.grabber.switch_source(
                _ConnectErrorAdapter(adapter.error or "ONVIF resolve failed.")
            )
            return
        sub = adapter.source
        main = adapter.main_source or sub
        try:
            with self.lock:
                self.cfg["source"] = sub
                self.cfg["main_source"] = main
                self.cfg["protocol"] = "onvif"
                if adapter.manufacturer:
                    self.cfg["vendor"] = adapter.manufacturer
                if camera_id or camera_name:
                    upsert_camera(
                        self.cfg,
                        {
                            "id": camera_id,
                            "name": camera_name or "Untitled camera",
                            "source": sub,
                            "main_source": main,
                            "protocol": "onvif",
                            "vendor": adapter.manufacturer or "generic",
                            "username": username,
                            "xaddrs": xaddrs,
                        },
                    )
                cfg_to_save = dict(self.cfg)
                active_id = str(self.cfg.get("active_camera_id") or camera_id or "")
            save_config(cfg_to_save)
            stream_id = self._bind_gateway(sub, active_id, main_source=main)
            inner = create_adapter(
                sub,
                gateway=self.media if stream_id else None,
                stream_id=stream_id,
                protocol="rtsp",
            )
            self.grabber.switch_source(inner)
        except Exception as exc:
            self.grabber.switch_source(_ConnectErrorAdapter(str(exc)))

    def apply_hub_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if "telegram_bot_token" in payload:
            updates["telegram_bot_token"] = str(payload.get("telegram_bot_token") or "")
        if "telegram_chat_id" in payload:
            updates["telegram_chat_id"] = str(payload.get("telegram_chat_id") or "")
        if "venue" in payload:
            updates["venue"] = str(payload.get("venue") or "").strip() or "Demo Garage"
            updates["garage_name"] = updates["venue"]
        if "garage_name" in payload:
            updates["garage_name"] = str(payload.get("garage_name") or "").strip() or "Demo Garage"
            updates["venue"] = updates["garage_name"]
        if "open_time" in payload:
            updates["open_time"] = parse_clock(payload.get("open_time"), "08:00")
        if "close_time" in payload:
            updates["close_time"] = parse_clock(payload.get("close_time"), "18:00")
        if "open_time" in updates or "close_time" in updates:
            updates["operating_hours"] = {
                "open": updates.get("open_time") or str(self.cfg.get("open_time") or "08:00"),
                "close": updates.get("close_time") or str(self.cfg.get("close_time") or "18:00"),
            }
        if "absent_seconds" in payload:
            try:
                updates["absent_seconds"] = max(5.0, min(600.0, float(payload.get("absent_seconds"))))
            except (TypeError, ValueError):
                return {"success": False, "error": "absent_seconds must be a number."}
        if not updates:
            return {"success": False, "error": "No settings provided."}
        with self.lock:
            self.cfg.update(updates)
            save_config(updates)
            if "telegram_bot_token" in updates or "telegram_chat_id" in updates:
                self.bot = TelegramOut(
                    self.cfg.get("telegram_bot_token", ""),
                    self.cfg.get("telegram_chat_id", ""),
                )
            snapshot = {key: self.cfg.get(key) for key in _SETTINGS_KEYS}
        snapshot["success"] = True
        snapshot["bays"] = list(self.cfg.get("bays") or [])
        snapshot["wifi_devices"] = list(self.cfg.get("wifi_devices") or [])
        snapshot["operating_hours"] = self.cfg.get("operating_hours")
        return snapshot

    def save_camera(self, fields: dict[str, Any]) -> dict[str, Any]:
        name = str(fields.get("name") or fields.get("camera_name") or "").strip()
        if not name:
            return {"success": False, "error": "Camera location name is required."}
        mapped = {
            "id": fields.get("id") or fields.get("camera_id") or "",
            "name": name,
            "source": fields.get("source", ""),
            "protocol": fields.get("protocol", ""),
            "vendor": fields.get("vendor", ""),
            "username": fields.get("username", ""),
            "rotate": fields.get("rotate", "auto"),
            "flip": fields.get("flip", "none"),
            "roi": fields.get("roi"),
        }
        if "main_source" in fields:
            mapped["main_source"] = fields.get("main_source", "")
        if "xaddrs" in fields:
            mapped["xaddrs"] = fields.get("xaddrs")
        if "bays" in fields:
            mapped["bays"] = fields.get("bays")
        if "enabled" in fields:
            mapped["enabled"] = bool(fields.get("enabled"))
        with self.lock:
            entry = upsert_camera(self.cfg, mapped)
            save_config(self.cfg)
        try:
            self.register_all_cameras_in_gateway()
        except Exception:
            pass
        if self.running:
            try:
                self.camera_pool.sync_cameras(list(self.cfg.get("cameras") or []))
            except Exception:
                pass
        return {
            "success": True,
            "camera": entry,
            "cameras": list(self.cfg.get("cameras") or []),
            "active_camera_id": entry["id"],
            "bays": entry.get("bays") or [],
        }

    def delete_camera(self, camera_id: Any) -> dict[str, Any]:
        cid = str(camera_id or "").strip()
        if not cid:
            return {"success": False, "error": "Camera id is required."}
        with self.lock:
            cameras = [c for c in _normalize_cameras(self.cfg.get("cameras")) if c["id"] != cid]
            self.cfg["cameras"] = cameras
            if str(self.cfg.get("active_camera_id") or "") == cid:
                self.cfg["active_camera_id"] = cameras[0]["id"] if cameras else ""
            if self._gateway_stream_id == sanitize_stream_id(cid):
                self._drop_gateway_stream()
            save_config(self.cfg)
        if self.running:
            try:
                self.camera_pool.sync_cameras(cameras)
            except Exception:
                pass
        return {
            "success": True,
            "cameras": cameras,
            "active_camera_id": str(self.cfg.get("active_camera_id") or ""),
        }

    def toggle_camera_port(self, camera_id: Any, enabled: bool | None = None) -> dict[str, Any]:
        cid = str(camera_id or "").strip()
        if not cid:
            return {"success": False, "error": "Camera id is required."}
        with self.lock:
            cameras = _normalize_cameras(self.cfg.get("cameras"))
            target = None
            for cam in cameras:
                if cam["id"] == cid:
                    target = cam
                    break
            if target is None:
                return {"success": False, "error": f"Camera '{cid}' not found."}
            current = target.get("enabled", True)
            new_state = (not current) if enabled is None else bool(enabled)
            target["enabled"] = new_state
            self.cfg["cameras"] = cameras
            save_config(self.cfg)
        if self.running or bool(self.camera_pool._workers):
            try:
                self.camera_pool.sync_cameras(cameras)
            except Exception:
                pass
        return {
            "success": True,
            "camera_id": cid,
            "enabled": new_state,
            "cameras": cameras,
            "active_camera_id": str(self.cfg.get("active_camera_id") or ""),
        }

    def set_roi(self, roi_value: Any) -> dict[str, Any]:
        parsed = parse_roi(roi_value)
        if parsed is None:
            return {"success": False, "error": "ROI must be [x, y, width, height] fractions."}
        with self.lock:
            self.cfg["roi"] = parsed
            bays = parse_bays(self.cfg.get("bays"), fallback_roi=parsed)
            if bays:
                bays[0] = {**bays[0], "roi": parsed}
            self.cfg["bays"] = bays
            cameras = _normalize_cameras(self.cfg.get("cameras"))
            active = str(self.cfg.get("active_camera_id") or "")
            for cam in cameras:
                if cam["id"] == active:
                    cam["roi"] = parsed
                    cam["bays"] = bays
                    break
            self.cfg["cameras"] = cameras
            self.bay_manager.set_bays(bays)
            self.bay_telemetry = self.bay_manager.telemetry()
            save_config(self.cfg)
        return {"success": True, "roi": parsed, "bays": bays, "cameras": cameras, "active_camera_id": active}

    def set_bays(self, bays_value: Any) -> dict[str, Any]:
        parsed = parse_bays(
            bays_value,
            fallback_roi=parse_roi(self.cfg.get("roi")),
            seed_if_empty=False,
        )
        with self.lock:
            previous = parse_bays(self.cfg.get("bays"), seed_if_empty=False)
            previous_ids = {str(bay.get("id") or "") for bay in previous}
            keep_ids = {str(bay.get("id") or "") for bay in parsed}
            removed_ids = [bay_id for bay_id in previous_ids if bay_id and bay_id not in keep_ids]
            self.cfg["bays"] = parsed
            if parsed:
                self.cfg["roi"] = list(parsed[0]["roi"])
            cameras = _normalize_cameras(self.cfg.get("cameras"))
            active = str(self.cfg.get("active_camera_id") or "")
            for cam in cameras:
                if cam["id"] == active:
                    cam["roi"] = self.cfg.get("roi")
                    cam["bays"] = parsed
                    break
            self.cfg["cameras"] = cameras
            self.bay_manager.set_bays(parsed)
            self.bay_telemetry = self.bay_manager.telemetry()
            save_config(self.cfg)
            roi = list(self.cfg.get("roi") or [0.30, 0.20, 0.40, 0.60])
        if removed_ids:
            try:
                conn = connect(DATA_DIR / "events.db", check_same_thread=False)
                try:
                    close_bay_sessions(conn, removed_ids, datetime.now())
                finally:
                    conn.close()
            except Exception as exc:
                print(f"[set_bays] close sessions failed: {exc}", flush=True)
        return {
            "success": True,
            "bays": parsed,
            "roi": roi,
            "cameras": cameras,
            "active_camera_id": active,
            "removed_bay_ids": removed_ids,
        }

    def import_bays(self, source_camera_id: Any, target_camera_id: Any = None) -> dict[str, Any]:
        src_id = str(source_camera_id or "").strip()
        if not src_id:
            return {"success": False, "error": "Source camera id is required."}
        with self.lock:
            cameras = _normalize_cameras(self.cfg.get("cameras"))
            tgt_id = str(target_camera_id or self.cfg.get("active_camera_id") or "").strip()
            if not tgt_id:
                return {"success": False, "error": "Target camera id is required."}
            src_cam = next((c for c in cameras if c["id"] == src_id), None)
            if not src_cam:
                return {"success": False, "error": f"Source camera '{src_id}' not found."}
            tgt_cam = next((c for c in cameras if c["id"] == tgt_id), None)
            if not tgt_cam:
                return {"success": False, "error": f"Target camera '{tgt_id}' not found."}

            src_bays = src_cam.get("bays") or []
            if not src_bays:
                src_roi = src_cam.get("roi") or [0.30, 0.20, 0.40, 0.60]
                src_bays = parse_bays(None, fallback_roi=src_roi, seed_if_empty=True)

            cloned = []
            for b in src_bays:
                item = dict(b)
                if "roi" in item and isinstance(item["roi"], list):
                    item["roi"] = list(item["roi"])
                if "polygon" in item and isinstance(item["polygon"], list):
                    item["polygon"] = [list(pt) for pt in item["polygon"]]
                cloned.append(item)

            tgt_cam["bays"] = cloned
            if tgt_id == str(self.cfg.get("active_camera_id") or ""):
                self.cfg["bays"] = cloned
                if cloned:
                    self.cfg["roi"] = list(cloned[0]["roi"])
                self.bay_manager.set_bays(cloned)
                self.bay_telemetry = self.bay_manager.telemetry()

            self.cfg["cameras"] = cameras
            save_config(self.cfg)
            active = str(self.cfg.get("active_camera_id") or "")
            roi = list(self.cfg.get("roi") or [0.30, 0.20, 0.40, 0.60])
        return {
            "success": True,
            "imported_bays_count": len(cloned),
            "bays": cloned,
            "cameras": cameras,
            "active_camera_id": active,
            "roi": roi,
            "message": f"Imported {len(cloned)} bays from {src_cam.get('name', src_id)}.",
        }

    def get_camera_frame(self, camera_id: str | None = None) -> tuple[bytes | None, str]:
        """Fetch latest JPEG frame for a specific camera.

        Returns (jpeg_bytes, content_type).
        - If camera_id is None, empty, or active_camera_id: returns the active AI-annotated frame.
        - If another camera: pulls directly from CameraStreamPool, falling back to go2rtc or request_still.
        Uses in-memory cache to prevent blocking and stream drops across concurrent grid tiles.
        """
        cid = str(camera_id or "").strip()
        active_id = str(self.cfg.get("active_camera_id") or "").strip()
        target_id = cid or active_id
        with self.lock:
            cams = list(self.cfg.get("cameras") or [])
        target_cam = next((c for c in cams if str(c.get("id")) == target_id), None)
        if target_cam and not target_cam.get("enabled", True):
            return None, "image/jpeg"
        now = time.time()

        if not cid or cid == active_id:
            with self.lock:
                frame_data = self.current_frame_jpeg
            if frame_data:
                self._camera_frame_cache[cid or active_id] = (frame_data, "image/jpeg", now)
                return frame_data, "image/jpeg"
            cached = self._camera_frame_cache.get(cid or active_id)
            if cached and (now - cached[2]) < 60.0:
                return cached[0], cached[1]
            worker = self.camera_pool.get_worker(cid or active_id)
            if worker and worker.latest_jpeg:
                return worker.latest_jpeg, "image/jpeg"
            if cached:
                return cached[0], cached[1]
            return None, "image/jpeg"

        # Background camera: query CameraStreamPool worker
        worker = self.camera_pool.get_worker(cid)
        if worker is None:
            with self.lock:
                cams = list(self.cfg.get("cameras") or [])
            self.camera_pool.sync_cameras(cams)
            worker = self.camera_pool.get_worker(cid)

        if worker:
            if worker.latest_jpeg:
                self._camera_frame_cache[cid] = (worker.latest_jpeg, "image/jpeg", worker.latest_ts)
                return worker.latest_jpeg, "image/jpeg"
            # Allow brief moment for initial frame acquisition
            for _ in range(8):
                if worker.latest_jpeg:
                    self._camera_frame_cache[cid] = (worker.latest_jpeg, "image/jpeg", worker.latest_ts)
                    return worker.latest_jpeg, "image/jpeg"
                time.sleep(0.05)

        cached = self._camera_frame_cache.get(cid)
        if cached and (now - cached[2]) < 60.0:
            return cached[0], cached[1]

        stream_id = sanitize_stream_id(cid)
        if self.media.is_ready():
            try:
                resp = requests.get(
                    f"{self.media.api_base}/api/frame.jpeg?src={urllib.parse.quote(stream_id, safe='')}",
                    timeout=1.5,
                )
                if resp.status_code == 200 and resp.content and len(resp.content) > 100:
                    self._camera_frame_cache[cid] = (resp.content, "image/jpeg", now)
                    return resp.content, "image/jpeg"
            except Exception:
                pass

        cam = None
        with self.lock:
            for c in self.cfg.get("cameras") or []:
                if c.get("id") == cid:
                    cam = dict(c)
                    break
        if cam and cam.get("source") is not None and str(cam.get("source")).strip() != "":
            src = cam["source"]
            try:
                still = request_still(src, gateway=self.media, stream_id=stream_id, timeout=2.0)
                if still is not None:
                    ok, buf = cv2.imencode(".jpg", still, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok:
                        data = buf.tobytes()
                        self._camera_frame_cache[cid] = (data, "image/jpeg", now)
                        return data, "image/jpeg"
            except Exception:
                pass

        if cached:
            return cached[0], cached[1]
        return None, "image/jpeg"

    def set_wifi_devices(self, devices_value: Any) -> dict[str, Any]:
        parsed = self.wifi.set_devices(devices_value)
        with self.lock:
            self.cfg["wifi_devices"] = parsed
            save_config(self.cfg)
        return {"success": True, "wifi_devices": parsed}

    def garage_telemetry(self) -> dict[str, Any]:
        with self.lock:
            bays = list(self.bay_telemetry or self.bay_manager.telemetry())
            cfg = dict(self.cfg)
            fps = self.fps
            staff = list(self.staff_names)
            identities = list(self.identities)
            last_seen = dict(self.last_face_seen)
        wifi_rows = self.wifi.snapshot()
        wifi_by_name = {row["name"]: row for row in wifi_rows}
        now = time.time()
        for bay in bays:
            name = bay.get("mechanic_name")
            face_recent = bool(name) and (now - last_seen.get(name, 0)) < 30
            wifi_on = self.wifi.is_connected(name) if name else False
            bay["presence"] = presence_status(face_recent, wifi_on)
            bay["wifi_connected"] = wifi_on
        active = sum(1 for bay in bays if bay.get("state") != "EMPTY")
        gname = garage_name_of(cfg)
        gopen = shop_is_open(cfg)
        return {
            "bays": bays,
            "fps": fps,
            "garage_name": gname,
            "shop_open": gopen,
            "garage": {
                "name": gname,
                "shop_open": gopen,
                "active_bay_count": active,
                "total_bays": len(bays),
            },
            "open_time": str(cfg.get("open_time") or "08:00"),
            "close_time": str(cfg.get("close_time") or "18:00"),
            "active_bay_count": active,
            "total_bays": len(bays),
            "staff_names": staff,
            "identities": identities,
            "wifi_devices": wifi_rows,
            "roi": list(cfg.get("roi") or [0.30, 0.20, 0.40, 0.60]),
        }

    def garage_scorecard(self) -> dict[str, Any]:
        cfg = dict(self.cfg)
        day = datetime.now().date()
        conn = connect(DATA_DIR / "events.db", check_same_thread=False)
        try:
            summary = get_daily_garage_summary(
                conn,
                day,
                open_time=str(cfg.get("open_time") or "08:00"),
                close_time=str(cfg.get("close_time") or "18:00"),
                bay_ids=[b["id"] for b in (cfg.get("bays") or DEFAULT_BAYS)],
            )
        finally:
            conn.close()
        wifi_rows = self.wifi.snapshot()
        wifi_by = {row["name"].lower(): row for row in wifi_rows}
        live = {b.get("mechanic_name"): b for b in self.garage_telemetry().get("bays") or [] if b.get("mechanic_name")}
        now = time.time()
        technicians = []
        for tech in summary["technicians"]:
            name = tech["staff_name"]
            wifi = wifi_by.get(name.lower(), {})
            face_recent = (now - self.last_face_seen.get(name, 0)) < 45
            wifi_on = bool(wifi.get("connected"))
            bay = live.get(name) or {}
            if tech["clocked_in"] and bay.get("state") == "WORKING":
                attendance = f"Active in {bay.get('name') or bay.get('bay_id')}"
            elif tech["clocked_in"] and bay.get("state") == "IDLE":
                attendance = "On Break"
            elif tech["clocked_in"] and wifi_on:
                attendance = "Clocked In"
            elif tech["clocked_in"]:
                attendance = "Clocked In"
            else:
                attendance = "Clocked Out"
            if not wifi_on and tech["clocked_in"] and not face_recent:
                attendance = "Off-site break"
            score = float(tech["performance_score"])
            technicians.append(
                {
                    **tech,
                    "attendance": attendance,
                    "wifi_connected": wifi_on,
                    "presence": presence_status(face_recent, wifi_on),
                    "current_bay": bay.get("bay_id"),
                    "wrench_pct": score,
                    "badge": "high" if score >= 70 else ("normal" if score >= 40 else "low"),
                }
            )
        enrolled = []
        try:
            enrolled = [row["name"] for row in list_identities(cfg)]
        except Exception:
            enrolled = list({t["staff_name"] for t in technicians})
        return {
            "date": summary["date"],
            "garage_name": garage_name_of(cfg),
            "shop": summary["shop"],
            "technicians": technicians,
            "bays": summary["bays"],
            "wifi_devices": wifi_rows,
            "enrolled": enrolled,
            "attendance_logs": technicians,
        }

    def set_orient(self, rotate: Any = None, flip: Any = None) -> dict[str, Any]:
        with self.lock:
            if rotate is not None and is_auto_rotate(rotate):
                self.cfg["rotate"] = "auto"
                self.cfg["flip"] = "none"
            else:
                if rotate is not None:
                    self.cfg["rotate"] = parse_rotate(rotate)
                if flip is not None:
                    self.cfg["flip"] = parse_flip(flip)
            cameras = _normalize_cameras(self.cfg.get("cameras"))
            active = str(self.cfg.get("active_camera_id") or "")
            for cam in cameras:
                if cam["id"] == active:
                    cam["rotate"] = self.cfg.get("rotate")
                    cam["flip"] = self.cfg.get("flip")
                    break
            self.cfg["cameras"] = cameras
            save_config(self.cfg)
            return {
                "success": True,
                "rotate": self.cfg.get("rotate"),
                "flip": self.cfg.get("flip"),
                "cameras": cameras,
                "active_camera_id": active,
            }

    def _sync_connection_from_grabber(self) -> tuple[str, str | None, int]:
        grabber = self.grabber
        state = grabber.connection_state
        error = grabber.error
        generation = grabber.generation
        self.connection_state = state
        if error:
            self.error_message = error
        if state == "CONNECTING":
            self.status_text = "CONNECTING"
        elif state == "RECONNECTING":
            self.status_text = "RECONNECTING..."
        elif state == "FAILED":
            self.status_text = "FAILED"
        return state, error, generation

    def _worker_loop(self):
        from ultralytics import YOLO

        print("[LiveStreamEngine] Loading YOLO pose model...")
        self.conn = connect(DATA_DIR / "events.db")
        self.runtime_profile = resolve_runtime(self.cfg)
        weights_path = resolve_weights_file(self.cfg, get_resource_path, DATA_DIR)
        veh_weights_path = get_resource_path("yolo11n.pt")
        if not veh_weights_path.exists():
            veh_weights_path = DATA_DIR / "yolo11n.pt"
        try:
            self.model = YOLO(str(weights_path))
            self.vehicle_model = YOLO(str(veh_weights_path) if veh_weights_path.exists() else "yolo11n.pt")
            print(
                f"[LiveStreamEngine] Models ready ({self.runtime_profile.name}, "
                f"device={self.runtime_profile.yolo_device}, weights={weights_path})"
            )
            self.face_rec = try_create_face_recognizer(self.cfg)
            self.reid = try_create_body_reid(self.cfg)
            self.tracker = PersonTracker(
                max_age=self.runtime_profile.track_max_age,
                min_hits=self.runtime_profile.track_min_hits,
                iou_threshold=self.runtime_profile.track_iou_threshold,
                reid_threshold=self.runtime_profile.reid_match_threshold,
            )
        except Exception as e:
            self.error_message = f"Failed to load YOLO model: {e}"
            with self.lock:
                self.connection_state = "FAILED"
                self.status_text = "FAILED"
            return

        proofs = DATA_DIR / "proofs"
        session_gen = -1
        source: int | str = 0
        absent = 10.0
        interval = 1.0 / 8.0
        person_conf = 0.35
        min_person_height = 0.12
        min_aspect = 1.1
        min_keypoints = 4
        kpt_conf = 0.4
        imgsz = 640
        ghost = GhostCounter(absent, 30.0)
        last_accepted: list[Detection] = []
        last_rejected: list[Detection] = []
        last_state = GhostState(False, 0.0, False)
        last_infer = 0.0
        prev_gray = None
        frame_count = 0
        t_fps = time.time()
        clock_out_grace = 600.0
        if self.conn is not None:
            summary = get_daily_garage_summary(
                self.conn,
                datetime.now().date(),
                bay_ids=[b["id"] for b in self.bay_manager.configs()],
            )
            self.bay_manager.hydrate_today(summary.get("bays") or [])

        while self.running:
            with self.lock:
                streaming = self.is_streaming
                cfg = dict(self.cfg)

            if not streaming:
                session_gen = -1
                time.sleep(0.2)
                continue

            state, err, generation = self._sync_connection_from_grabber()
            if state == "FAILED" and generation != session_gen:
                with self.lock:
                    if self.grabber.generation != generation:
                        continue
                    session_gen = generation
                    self.is_streaming = False
                    self.current_frame_jpeg = None
                    self.error_message = err
                    self.status_text = "FAILED"
                    self.connection_state = "FAILED"
                    self.new_frame_event.set()
                continue

            packet = self.grabber.get_latest_frame(timeout=0.1)
            if packet is None:
                continue

            generation = self.grabber.generation
            active_cid = str(self.cfg.get("active_camera_id") or "")
            if generation != session_gen or active_cid != getattr(self, "_last_worker_cam_id", None):
                session_gen = generation
                self._last_worker_cam_id = active_cid
                source = parse_source(cfg.get("source", 0))
                absent = float(cfg.get("absent_seconds") or 10)
                cooldown = float(cfg.get("cooldown_seconds") or 30)
                detect_fps = max(0.5, float(cfg.get("detect_fps") or 8.0))
                interval = 1.0 / detect_fps
                person_conf = float(cfg.get("person_conf") if cfg.get("person_conf") is not None else 0.35)
                min_person_height = float(
                    cfg.get("min_person_height") if cfg.get("min_person_height") is not None else 0.12
                )
                min_aspect = float(cfg.get("min_aspect") if cfg.get("min_aspect") is not None else 1.1)
                min_keypoints = int(cfg.get("min_keypoints") if cfg.get("min_keypoints") is not None else 4)
                kpt_conf = float(cfg.get("kpt_conf") if cfg.get("kpt_conf") is not None else 0.4)
                imgsz = max(32, int(cfg.get("imgsz") or 640) // 32 * 32)
                confirm = float(
                    cfg.get("occupy_confirm_seconds")
                    if cfg.get("occupy_confirm_seconds") is not None
                    else 1.0
                )
                clear = float(
                    cfg.get("occupy_clear_seconds") if cfg.get("occupy_clear_seconds") is not None else 1.0
                )
                clock_out_grace = float(cfg.get("clock_out_seconds") or 600)
                idle_hold = float(cfg.get("idle_stationary_seconds") or 120)
                print(f"[LiveStreamEngine] Ingesting camera stream: {redact_source(source)}")
                ghost = GhostCounter(absent, cooldown)
                self.bay_manager.confirm = confirm
                self.bay_manager.clear = clear
                self.bay_manager.idle_stationary_seconds = idle_hold
                self.bay_manager.set_bays(cfg.get("bays"), fallback_roi=parse_roi(cfg.get("roi")))
                last_accepted = []
                last_rejected = []
                last_state = GhostState(False, 0.0, False)
                last_infer = 0.0
                frame_count = 0
                t_fps = time.time()
                if self.tracker is not None:
                    self.tracker.reset()
                rotate_deg, flip = resolve_orient(cfg.get("rotate"), source, packet.frame, cfg.get("flip"))
                first_oriented = orient_frame(packet.frame, rotate_deg, flip)
                h0, w0 = first_oriented.shape[:2]
                with self.lock:
                    self.error_message = None
                    self.status_text = "CONNECTED"
                    self.connection_state = "CONNECTED"
                    self.stream_resolution = f"{w0}x{h0}"

            with self.lock:
                venue = garage_name_of(self.cfg)
                rotate_now = self.cfg.get("rotate")
                flip_now = self.cfg.get("flip")

            frame = packet.frame
            rotate_deg, flip = resolve_orient(rotate_now, source, frame, flip_now)
            frame = orient_frame(frame, rotate_deg, flip)
            h, w = frame.shape[:2]
            self.stream_resolution = f"{w}x{h}"

            now = time.time()
            frame_count += 1
            if now - t_fps >= 1.0:
                self.fps = frame_count / (now - t_fps)
                frame_count = 0
                t_fps = now

            snapshots = self.bay_manager.snapshots()
            any_occupied = any(s.state != "EMPTY" for s in snapshots)

            # Fast Motion Scanner (<0.05ms CPU on 160x120 grayscale)
            try:
                small_gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 120))
                if prev_gray is not None:
                    diff = cv2.absdiff(prev_gray, small_gray)
                    motion_score = float(cv2.mean(diff)[0])
                else:
                    motion_score = 10.0
                prev_gray = small_gray
            except Exception as ex:
                motion_score = 10.0

            # Dynamic AI Cadence: High FPS during active motion/work; Low-compute Sleep during static wait
            has_active_work = any(s.state in ("WORKING", "UNDER_VEHICLE") for s in snapshots)
            if motion_score > 1.2 or has_active_work:
                dynamic_interval = interval  # Full high-cadence AI (8 FPS)
            elif any(s.state in ("PARKED_WAITING", "ON_BREAK") for s in snapshots):
                dynamic_interval = 2.0  # Low-compute check (0.5 FPS during customer consultation wait)
            else:
                dynamic_interval = 3.0  # Idle Sleep Mode (wakes up instantly on motion)

            if now - last_infer >= dynamic_interval:
                last_infer = now
                try:
                    t_pred = time.perf_counter()
                    result = self.model.predict(
                        frame,
                        imgsz=imgsz,
                        conf=person_conf,
                        device=self.runtime_profile.yolo_device if self.runtime_profile else None,
                        verbose=False,
                    )[0]
                    vehicles = []
                    if getattr(self, "vehicle_model", None) is not None:
                        try:
                            veh_res = self.vehicle_model.predict(
                                frame,
                                imgsz=imgsz,
                                classes=[2, 3, 5, 7],
                                conf=0.18,
                                device=None,
                                verbose=False,
                            )[0]
                            vehicles = extract_vehicle_detections(veh_res, w, h, conf_min=0.18)
                        except Exception as ex:
                            print(f"[VehicleInfer] Prediction error: {ex}")

                    departed_bays = self.bay_manager.sync_auto_vehicles(vehicles, w, h, now=now)
                    if departed_bays and self.conn is not None:
                        for dep_id in departed_bays:
                            for bay_cfg in self.bay_manager.configs():
                                if bay_cfg.get("id") == dep_id and bay_cfg.get("job_id"):
                                    try:
                                        complete_vehicle_job(self.conn, bay_cfg["job_id"])
                                        eval_report = evaluate_completed_vehicle_job(self.conn, bay_cfg["job_id"])
                                        if eval_report:
                                            print(f"[Performance Evaluation] Job {eval_report.job_id}: Grade={eval_report.performance_grade}, Score={eval_report.performance_score}, Efficiency={eval_report.efficiency_pct:.1f}% by {eval_report.primary_technician}")
                                    except Exception as ex:
                                        print(f"[Vehicle Departure] Error completing/evaluating job {bay_cfg.get('job_id')}: {ex}")
                    last_accepted, last_rejected = person_detections(
                        result,
                        h,
                        conf_min=person_conf,
                        min_height_frac=min_person_height,
                        min_aspect=min_aspect,
                        min_keypoints=min_keypoints,
                        kpt_conf=kpt_conf,
                    )
                    if self.tracker is not None:
                        last_accepted = run_identity_pipeline(
                            frame,
                            last_accepted,
                            self.tracker,
                            face_rec=self.face_rec,
                            reid=self.reid,
                        )
                    elif self.face_rec is not None:
                        self.face_rec.annotate_detections(frame, last_accepted)
                    snapshots = self.bay_manager.update(
                        last_accepted, w, h, now, kpt_conf=kpt_conf
                    )
                    any_occupied = any(s.state != "EMPTY" for s in snapshots)
                    last_state = ghost.update(any_occupied, now)
                    stamp = datetime.now()
                    self._record_garage_tick(last_accepted, snapshots, last_state, stamp, now, clock_out_grace)

                    self.is_occupied = last_state.occupied
                    self.empty_elapsed = last_state.empty_elapsed
                    self.person_count = len(last_accepted)
                    self.staff_names = [
                        det.identity for det in last_accepted if det.is_staff and det.identity
                    ]
                    self.identities = [det.identity or "person" for det in last_accepted]
                    working = [s for s in snapshots if s.state == "WORKING"]
                    if working:
                        names = ", ".join(s.mechanic_name or s.name for s in working)
                        self.status_text = f"WRENCH TIME [{names}]"
                    elif any_occupied:
                        self.status_text = till_status_label(
                            True,
                            last_accepted,
                            self.empty_elapsed,
                            absent,
                            face_id_enabled=self.face_rec is not None,
                        )
                    else:
                        self.status_text = "SHOP FLOOR EMPTY"
                    with self.lock:
                        self.bay_telemetry = [s.as_dict() for s in snapshots]

                    if self.conn is not None:
                        upsert_minute(
                            self.conn,
                            stamp.strftime("%Y-%m-%d %H:%M"),
                            len(last_accepted),
                            last_state.occupied,
                        )
                        if last_state.occupied and not has_opened_today(self.conn, stamp.date()):
                            insert_event(self.conn, "opened", stamp)

                        if last_state.should_alert:
                            primary = snapshots[0] if snapshots else None
                            roi_px = roi_to_pixels(w, h, primary.roi if primary else [0.3, 0.2, 0.4, 0.6])
                            proof_frame = frame
                            main_src = str(cfg.get("main_source") or "").strip()
                            if main_src:
                                still = request_still(
                                    main_src,
                                    gateway=self.media,
                                    stream_id=self._gateway_main_stream_id,
                                    timeout=1.5,
                                )
                                if still is not None:
                                    sh, sw = still.shape[:2]
                                    roi_px = scale_roi_px(roi_px, (w, h), (sw, sh))
                                    proof_frame = still
                            path = save_proof(proof_frame, roi_px, stamp, proofs, kind="idle_bay")
                            insert_event(self.conn, "abandoned", stamp, str(path))
                            caption = (
                                f"{venue}: no active wrench time "
                                f"for {int(absent)}s.\n{stamp.strftime('%Y-%m-%d %H:%M:%S')}"
                            )
                            print(f"[LiveStreamEngine Alert] {path}")
                            self.bot.send_photo(path, caption)
                except Exception as exc:
                    print(f"[LiveStreamEngine Infer Error] {exc}")

            annotated = frame.copy()
            bay_cfgs = self.bay_manager.configs()
            for det in last_rejected:
                det_in_roi = any(detection_in_bay(det, b, w, h, kpt_conf) for b in bay_cfgs)
                draw_detection(
                    annotated, det, in_roi=det_in_roi, kpt_conf=kpt_conf
                )
            for det in last_accepted:
                det_in_roi = any(detection_in_bay(det, b, w, h, kpt_conf) for b in bay_cfgs)
                draw_detection(
                    annotated,
                    det,
                    in_roi=det_in_roi,
                    kpt_conf=kpt_conf,
                )

            # ROI geometry is rendered as an interactive DOM overlay in hub.html.
            # Do not burn boxes, handles, or labels into the JPEG — that duplicates
            # the overlay and leaves a lagging ghost box when the user drags.

            success, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if success:
                with self.lock:
                    self.current_frame_jpeg = buffer.tobytes()
                    self.frame_seq += 1
                self.new_frame_event.set()

        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _record_garage_tick(
        self,
        detections: list[Detection],
        snapshots,
        last_state: GhostState,
        stamp: datetime,
        now: float,
        clock_out_grace: float,
    ) -> None:
        if self.conn is None:
            return
        for det in detections:
            if det.is_staff and det.identity:
                self.last_face_seen[det.identity] = now
                record_face_clock_in(self.conn, det.identity, stamp)

        occupied_ids: set[str] = set()
        for tick in self.bay_manager.activity_ticks():
            bay_id = tick[0]
            technician = tick[1]
            is_working = tick[2]
            dt = tick[3]
            state = tick[4] if len(tick) > 4 else ("WORKING" if is_working else "IDLE")
            job_id = tick[5] if len(tick) > 5 else None

            if dt <= 0:
                continue
            if state != "EMPTY":
                occupied_ids.add(bay_id)
            update_technician_activity(self.conn, technician, bay_id, is_working, dt, stamp)

            if bay_id:
                active_job = job_id or get_or_create_vehicle_job(
                    self.conn, bay_id, primary_technician=technician, timestamp=stamp
                )
                active_dt = dt if state in ("WORKING", "UNDER_VEHICLE") else 0.0
                break_dt = dt if state == "ON_BREAK" else 0.0
                update_vehicle_job_activity(
                    self.conn, active_job, active_dt, break_dt, technician, stamp, status=state
                )
        close_empty_bays(self.conn, occupied_ids, stamp)

        wifi_dt = 0.0
        if self._wifi_last_tick is not None:
            wifi_dt = max(0.0, min(now - self._wifi_last_tick, 120.0))
        self._wifi_last_tick = now
        for row in self.wifi.snapshot():
            if row.get("connected") and wifi_dt > 0:
                add_wifi_minutes(self.conn, row["name"], wifi_dt, stamp)

        for departed in self.wifi.departures():
            last = self.last_face_seen.get(departed, 0.0)
            if now - last >= min(clock_out_grace, 90.0):
                record_face_clock_out(self.conn, departed, stamp)

        for name, last in list(self.last_face_seen.items()):
            if now - last < clock_out_grace:
                continue
            if self.wifi.is_connected(name):
                continue
            record_face_clock_out(self.conn, name, stamp)


GLOBAL_ENGINE = LiveStreamEngine()


HUB_HTML_PATH = get_resource_path("hub.html")


def load_hub_html() -> bytes:
    return HUB_HTML_PATH.read_text(encoding="utf-8").encode("utf-8")


def encode_mjpeg_part(frame_bytes: bytes, timestamp: float | None = None) -> bytes:
    """One multipart MJPEG part with Content-Length so libsoup/WebKit can frame it."""
    ts = time.time() if timestamp is None else timestamp
    header = (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n"
        + f"Content-Length: {len(frame_bytes)}\r\n".encode("ascii")
        + f"X-Timestamp: {ts:.3f}\r\n".encode("ascii")
        + b"\r\n"
    )
    return header + frame_bytes + b"\r\n"


class DashboardRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _cors(self) -> None:
        origin = self.headers.get("Origin") or "*"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")

    def end_headers(self):
        self._cors()
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def _identity_cfg(self) -> dict[str, Any]:
        return GLOBAL_ENGINE.cfg or read_config()

    def _identity_photo_url(self, name: str, filename: str) -> str:
        return (
            "/api/identities/"
            + urllib.parse.quote(name)
            + "/photo/"
            + urllib.parse.quote(filename)
        )

    def _enrich_identity(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        thumb = out.get("thumbnail")
        if thumb:
            out["thumbnail"] = self._identity_photo_url(out["name"], str(thumb))
        photos = []
        for photo in out.get("photos") or []:
            fname = photo.get("filename") if isinstance(photo, dict) else None
            if not fname:
                continue
            photos.append({"filename": fname, "url": self._identity_photo_url(out["name"], fname)})
        if "photos" in out:
            out["photos"] = photos
        return out

    def _handle_identities_get(self, parts: list[str]) -> bool:
        cfg = self._identity_cfg()
        try:
            if parts == ["api", "identities"]:
                rows = [self._enrich_identity(row) for row in list_identities(cfg)]
                self._send_json({"identities": rows})
                return True
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "identities":
                row = self._enrich_identity(get_identity(parts[2], cfg))
                self._send_json(row)
                return True
            if (
                len(parts) == 5
                and parts[0] == "api"
                and parts[1] == "identities"
                and parts[3] == "photo"
            ):
                path = identity_photo_path(parts[2], parts[4], cfg)
                data = path.read_bytes()
                mime = "image/jpeg"
                suffix = path.suffix.lower()
                if suffix == ".png":
                    mime = "image/png"
                elif suffix == ".webp":
                    mime = "image/webp"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return True
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, 404)
            return True
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
            return True
        return False

    def _parse_multipart_files(self, content_type: str, body: bytes) -> list[tuple[str, bytes]]:
        from email import policy
        from email.parser import BytesParser

        header = f"MIME-Version: 1.0\r\nContent-Type: {content_type}\r\n\r\n".encode("utf-8")
        msg = BytesParser(policy=policy.default).parsebytes(header + body)
        files: list[tuple[str, bytes]] = []
        if not msg.is_multipart():
            return files
        for part in msg.iter_parts():
            filename = part.get_filename()
            payload = part.get_payload(decode=True)
            if filename and payload:
                files.append((filename, payload))
        return files

    def _handle_identities_write(self, parts: list[str], method: str, payload: dict, files: list[tuple[str, bytes]]) -> bool:
        cfg = self._identity_cfg()
        try:
            if method == "POST" and parts == ["api", "identities"]:
                row = self._enrich_identity(create_identity(str(payload.get("name") or ""), cfg))
                GLOBAL_ENGINE.reload_face_id()
                self._send_json(row, 201)
                return True
            if (
                method == "POST"
                and len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "identities"
                and parts[3] == "photos"
            ):
                saved = []
                errors = []
                for filename, data in files:
                    try:
                        stored = save_identity_photo(parts[2], data, filename, cfg)
                        saved.append(
                            {
                                "filename": stored,
                                "url": self._identity_photo_url(parts[2], stored),
                            }
                        )
                    except Exception as exc:
                        errors.append({"filename": filename, "error": str(exc)})
                GLOBAL_ENGINE.reload_face_id()
                status = 200 if saved else 400
                self._send_json({"saved": saved, "errors": errors}, status)
                return True
            if method == "DELETE" and len(parts) == 3 and parts[0] == "api" and parts[1] == "identities":
                delete_identity(parts[2], cfg)
                GLOBAL_ENGINE.reload_face_id()
                self._send_json({"ok": True})
                return True
            if (
                method == "DELETE"
                and len(parts) == 5
                and parts[0] == "api"
                and parts[1] == "identities"
                and parts[3] == "photo"
            ):
                delete_identity_photo(parts[2], parts[4], cfg)
                GLOBAL_ENGINE.reload_face_id()
                self._send_json({"ok": True})
                return True
        except FileExistsError as exc:
            self._send_json({"error": str(exc)}, 409)
            return True
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, 404)
            return True
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
            return True
        return False

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(load_hub_html())

        elif parsed.path in (
            "/favicon.ico",
            "/favicon.png",
            "/favicon.svg",
            "/inb_surveillance.png",
            "/inb_surveillance.jpg",
            "/INB Surveillance.jpg",
        ):
            filename = parsed.path.lstrip("/")
            candidates = [
                ROOT / "public" / filename,
                ROOT.parent / "public" / filename,
                ROOT / filename,
                ROOT.parent / filename,
                Path(filename),
            ]
            content = None
            for cand in candidates:
                if cand.exists() and cand.is_file():
                    try:
                        content = cand.read_bytes()
                        break
                    except Exception:
                        pass
            if content:
                content_type = "image/png"
                if filename.endswith(".ico"):
                    content_type = "image/x-icon"
                elif filename.endswith(".svg"):
                    content_type = "image/svg+xml"
                elif filename.endswith(".jpg") or filename.endswith(".jpeg"):
                    content_type = "image/jpeg"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()

        elif parsed.path == "/api/config":
            cfg = read_config()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(cfg).encode("utf-8"))

        elif parsed.path == "/api/telemetry":
            grabber = GLOBAL_ENGINE.grabber
            connection = grabber.connection_state
            if connection == "STANDBY":
                connection = GLOBAL_ENGINE.connection_state
            sid = GLOBAL_ENGINE._gateway_stream_id
            garage = GLOBAL_ENGINE.garage_telemetry()
            protocol = str(
                GLOBAL_ENGINE.cfg.get("protocol")
                or protocol_from_source(GLOBAL_ENGINE.cfg.get("source"))
            )
            data = {
                "occupied": GLOBAL_ENGINE.is_occupied,
                "empty_elapsed": GLOBAL_ENGINE.empty_elapsed,
                "person_count": GLOBAL_ENGINE.person_count,
                "staff_names": GLOBAL_ENGINE.staff_names,
                "identities": GLOBAL_ENGINE.identities,
                "fps": GLOBAL_ENGINE.fps,
                "ingest_fps": grabber.ingest_fps,
                "infer_ms": round(float(GLOBAL_ENGINE.infer_ms or 0.0), 1),
                "protocol": protocol,
                "main_stream": bool(str(GLOBAL_ENGINE.cfg.get("main_source") or "").strip()),
                "status": GLOBAL_ENGINE.status_text,
                "connection": connection,
                "resolution": GLOBAL_ENGINE.stream_resolution,
                "error": grabber.error or GLOBAL_ENGINE.error_message,
                "roi": list(GLOBAL_ENGINE.cfg.get("roi") or [0.30, 0.20, 0.40, 0.60]),
                "bays": garage.get("bays"),
                "garage": {
                    "name": garage.get("garage_name"),
                    "shop_open": garage.get("shop_open"),
                    "active_bay_count": garage.get("active_bay_count"),
                    "total_bays": garage.get("total_bays"),
                },
                "stream_id": sid,
                "media": GLOBAL_ENGINE.media.status(sid),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        elif parsed.path == "/api/garage/telemetry":
            data = GLOBAL_ENGINE.garage_telemetry()
            grabber = GLOBAL_ENGINE.grabber
            data.update(
                {
                    "fps": GLOBAL_ENGINE.fps,
                    "ingest_fps": grabber.ingest_fps,
                    "infer_ms": round(float(GLOBAL_ENGINE.infer_ms or 0.0), 1),
                    "protocol": str(
                        GLOBAL_ENGINE.cfg.get("protocol")
                        or protocol_from_source(GLOBAL_ENGINE.cfg.get("source"))
                    ),
                    "main_stream": bool(str(GLOBAL_ENGINE.cfg.get("main_source") or "").strip()),
                    "status": GLOBAL_ENGINE.status_text,
                    "connection": grabber.connection_state
                    if grabber.connection_state != "STANDBY"
                    else GLOBAL_ENGINE.connection_state,
                    "resolution": GLOBAL_ENGINE.stream_resolution,
                    "error": grabber.error or GLOBAL_ENGINE.error_message,
                    "person_count": GLOBAL_ENGINE.person_count,
                }
            )
            self._send_json(data)

        elif parsed.path == "/api/garage/scorecard":
            self._send_json(GLOBAL_ENGINE.garage_scorecard())

        elif parsed.path == "/api/garage/evaluations":
            evals = []
            if GLOBAL_ENGINE.conn is not None:
                try:
                    rows = GLOBAL_ENGINE.conn.execute(
                        """
                        SELECT job_id, vehicle_type, primary_technician, technicians_json,
                               total_wrench_seconds, total_break_seconds, efficiency_pct,
                               performance_grade, performance_score, summary_notes,
                               started_at, completed_at
                        FROM vehicle_job_evaluations
                        ORDER BY id DESC LIMIT 50
                        """
                    ).fetchall()
                    for r in rows:
                        evals.append({
                            "job_id": r[0],
                            "vehicle_type": r[1],
                            "primary_technician": r[2],
                            "technicians": json.loads(r[3]) if r[3] else {},
                            "total_wrench_seconds": r[4],
                            "total_break_seconds": r[5],
                            "efficiency_pct": r[6],
                            "performance_grade": r[7],
                            "performance_score": r[8],
                            "summary_notes": r[9],
                            "started_at": r[10],
                            "completed_at": r[11],
                        })
                except Exception as ex:
                    print(f"[API] Error loading evaluations: {ex}")
            self._send_json({"evaluations": evals})

        elif parsed.path == "/api/garage/templates":
            self._send_json({"templates": KNOWLEDGE_BASE.service_templates})

        elif parsed.path == "/api/discovery/results":
            self._send_json(GLOBAL_ENGINE.discovery.results())

        elif parsed.path == "/api/uploaded-videos":
            init_videos_dir()
            videos = []
            allowed_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts"}
            if VIDEOS_DIR.is_dir():
                for f in sorted(VIDEOS_DIR.iterdir()):
                    if f.is_file() and f.suffix.lower() in allowed_exts:
                        videos.append({
                            "name": f.name,
                            "path": str(f.resolve()),
                            "size": f.stat().st_size,
                        })
            self._send_json({"videos": videos})

        elif parsed.path == "/api/frame.jpeg" or parsed.path.startswith("/api/camera/"):
            cam_id = None
            if parsed.path.startswith("/api/camera/"):
                parts = [p for p in parsed.path.strip("/").split("/") if p]
                if len(parts) >= 3 and parts[2] != "frame.jpeg":
                    cam_id = urllib.parse.unquote(parts[2])
                elif len(parts) >= 2 and parts[1] not in ("camera", "frame.jpeg"):
                    cam_id = urllib.parse.unquote(parts[1])
            if not cam_id and parsed.query:
                q = dict(urllib.parse.parse_qsl(parsed.query))
                cam_id = q.get("camera_id") or q.get("src") or q.get("id")

            frame_bytes, ctype = GLOBAL_ENGINE.get_camera_frame(cam_id)
            if not frame_bytes:
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(frame_bytes)))
            self.send_header("Cache-Control", "no-cache, private, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(frame_bytes)

        elif parsed.path == "/api/stream":
            # MJPEG kept for non-WebKit clients. Desktop hub uses /api/frame.jpeg
            # instead — WebKitGTK segfaults on long-lived multipart/x-mixed-replace.
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, private, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()

            try:
                self.connection.settimeout(2.0)
            except Exception:
                pass

            last_seq = -1
            stream_camera_id = str(GLOBAL_ENGINE.cfg.get("active_camera_id") or "")
            stream_generation = int(getattr(GLOBAL_ENGINE, "mjpeg_generation", 0) or 0)
            idle_ticks = 0
            try:
                while GLOBAL_ENGINE.running:
                    # If active camera switched or a same-camera reconnect started,
                    # terminate this old stream so the browser frees the connection slot.
                    cur_cam = str(GLOBAL_ENGINE.cfg.get("active_camera_id") or "")
                    cur_gen = int(getattr(GLOBAL_ENGINE, "mjpeg_generation", 0) or 0)
                    if cur_cam != stream_camera_id or cur_gen != stream_generation:
                        break

                    with GLOBAL_ENGINE.lock:
                        cur_seq = GLOBAL_ENGINE.frame_seq
                        frame_bytes = GLOBAL_ENGINE.current_frame_jpeg

                    if cur_seq != last_seq and frame_bytes is not None:
                        last_seq = cur_seq
                        idle_ticks = 0
                        self.wfile.write(encode_mjpeg_part(frame_bytes))
                        self.wfile.flush()
                    else:
                        idle_ticks += 1
                        # If frame is absent for > 8 seconds (400 * 0.02s), terminate to free socket
                        if idle_ticks > 400:
                            break
                    time.sleep(0.02)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, socket.error, OSError):
                pass

        elif parsed.path == "/api/garage/jobs":
            self._send_json(list_vehicle_jobs(GLOBAL_ENGINE.conn))

        elif parsed.path.startswith("/api/garage/jobs/") and parsed.path.endswith("/history"):
            parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
            job_id = parts[3] if len(parts) >= 4 else ""
            res = get_vehicle_job_history(GLOBAL_ENGINE.conn, job_id)
            if res:
                self._send_json(res)
            else:
                self._send_json({"error": "Job not found"}, 404)

        elif self._handle_identities_get(
            [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        ):
            return

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        length = int(self.headers.get("Content-Length", 0) or 0)
        max_upload_size = 1024 * 1024 * 1024 if parsed.path == "/api/upload-video" else 40 * 1024 * 1024
        if length > max_upload_size:
            self._send_json({"error": "Upload is too large."}, 413)
            return
        body = self.rfile.read(length) if length > 0 else b""
        content_type = self.headers.get("Content-Type") or ""
        files: list[tuple[str, bytes]] = []
        payload: dict[str, Any] = {}
        raw_json: Any = {}
        if "multipart/form-data" in content_type:
            files = self._parse_multipart_files(content_type, body)
        else:
            try:
                raw_json = json.loads(body.decode("utf-8") or "{}")
            except Exception:
                raw_json = {}
            payload = raw_json if isinstance(raw_json, dict) else {}

        if self._handle_identities_write(parts, "POST", payload, files):
            return

        if parsed.path == "/api/garage/jobs/complete":
            job_id = str(payload.get("job_id") or "").strip()
            ok = complete_vehicle_job(GLOBAL_ENGINE.conn, job_id)
            self._send_json({"ok": ok, "job_id": job_id})
            return

        if parsed.path in ("/api/connect-stream", "/api/save"):
            result = GLOBAL_ENGINE.connect_camera(payload)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))

        elif parsed.path == "/api/discovery/scan":
            self._send_json(GLOBAL_ENGINE.discovery.start_scan())

        elif parsed.path in ("/api/settings", "/api/config"):
            result = GLOBAL_ENGINE.apply_hub_settings(payload)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))

        elif parsed.path == "/api/cameras":
            result = GLOBAL_ENGINE.save_camera(payload)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))

        elif parsed.path == "/api/cameras/delete":
            result = GLOBAL_ENGINE.delete_camera(payload.get("id"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))

        elif parsed.path in ("/api/cameras/toggle-port", "/api/cameras/toggle"):
            result = GLOBAL_ENGINE.toggle_camera_port(payload.get("id"), payload.get("enabled"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))

        elif parsed.path == "/api/orient":
            result = GLOBAL_ENGINE.set_orient(payload.get("rotate"), payload.get("flip"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))

        elif parsed.path == "/api/roi":
            result = GLOBAL_ENGINE.set_roi(payload.get("roi"))
            self._send_json(result)

        elif parsed.path == "/api/garage/bays":
            bays_payload = raw_json if isinstance(raw_json, list) else (
                payload.get("bays") if "bays" in payload else payload
            )
            result = GLOBAL_ENGINE.set_bays(bays_payload)
            self._send_json(result)

        elif parsed.path == "/api/cameras/import-bays":
            src_id = payload.get("source_camera_id") or payload.get("source_id")
            tgt_id = payload.get("target_camera_id") or payload.get("target_id")
            result = GLOBAL_ENGINE.import_bays(src_id, tgt_id)
            self._send_json(result)

        elif parsed.path == "/api/upload-video":
            init_videos_dir()
            filename = ""
            file_content = b""

            params = urllib.parse.parse_qs(parsed.query)
            if "filename" in params:
                filename = params["filename"][0]

            if files:
                filename = filename or files[0][0]
                file_content = files[0][1]
            elif "multipart/form-data" in content_type and b"filename=" in body:
                match = re.search(rb'filename="([^"]+)"', body)
                if match:
                    filename = match.group(1).decode("utf-8", errors="replace")
                parts = body.split(b"\r\n\r\n", 1)
                if len(parts) == 2:
                    raw_data = parts[1]
                    boundary_end = raw_data.rfind(b"\r\n--")
                    file_content = raw_data[:boundary_end] if boundary_end != -1 else raw_data
                else:
                    file_content = body
            else:
                file_content = body

            if not filename:
                filename = f"upload_{int(time.time())}.mp4"

            safe_name = Path(filename).name
            dest = VIDEOS_DIR / safe_name
            dest.write_bytes(file_content)

            self._send_json({
                "success": True,
                "filename": safe_name,
                "path": str(dest.resolve()),
                "size": len(file_content),
            })

        elif parsed.path == "/api/garage/wifi":
            devices = raw_json if isinstance(raw_json, list) else payload.get("wifi_devices")
            if devices is None:
                devices = payload.get("devices")
            if devices is None and payload.get("name"):
                existing = list(GLOBAL_ENGINE.cfg.get("wifi_devices") or [])
                existing.append(payload)
                devices = existing
            result = GLOBAL_ENGINE.set_wifi_devices(devices)
            self._send_json(result)

        elif parsed.path == "/api/test-telegram":
            token = str(payload.get("token") or payload.get("telegram_bot_token") or "").strip()
            chat = str(payload.get("chat_id") or payload.get("telegram_chat_id") or "").strip()
            venue = str(payload.get("venue") or payload.get("garage_name") or "Demo Garage").strip()

            if not token or not chat:
                res = {"success": False, "error": "Bot Token and Chat ID are required."}
            else:
                GLOBAL_ENGINE.apply_hub_settings({
                    "telegram_bot_token": token,
                    "telegram_chat_id": chat,
                })
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                proofs_dir = DATA_DIR / "proofs"
                proofs_dir.mkdir(parents=True, exist_ok=True)
                proof_path = proofs_dir / f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

                frame_bytes = None
                with GLOBAL_ENGINE.lock:
                    if GLOBAL_ENGINE.current_frame_jpeg is not None:
                        frame_bytes = GLOBAL_ENGINE.current_frame_jpeg

                if frame_bytes is None and GLOBAL_ENGINE.is_streaming:
                    GLOBAL_ENGINE.new_frame_event.wait(timeout=2.0)
                    with GLOBAL_ENGINE.lock:
                        frame_bytes = GLOBAL_ENGINE.current_frame_jpeg

                if frame_bytes is None:
                    res = {"success": False, "error": "Could not capture camera frame. Please connect to a camera stream first."}
                else:
                    with proof_path.open("wb") as pf:
                        pf.write(frame_bytes)

                    status_str = GLOBAL_ENGINE.status_text or (
                        "STAFF IN ROI" if GLOBAL_ENGINE.is_occupied else "EMPTY"
                    )
                    identified = ", ".join(GLOBAL_ENGINE.identities) or "none"
                    caption = (
                        f"🔧 *Inbound Garage Snapshot*\n\n"
                        f"🏢 *Shop:* {venue}\n"
                        f"⏰ *Timestamp:* {now_str}\n"
                        f"🛠️ *Floor Status:* {status_str}\n"
                        f"👥 *Detections:* {GLOBAL_ENGINE.person_count} Person(s)\n"
                        f"🪪 *Identified:* {identified}\n"
                    )

                    url = f"https://api.telegram.org/bot{token}/sendPhoto"
                    try:
                        with proof_path.open("rb") as handle:
                            r = requests.post(
                                url,
                                data={"chat_id": chat, "caption": caption, "parse_mode": "Markdown"},
                                files={"photo": handle},
                                timeout=30,
                            )
                        data = r.json()
                        if r.status_code == 200 and data.get("ok"):
                            res = {
                                "success": True,
                                "message": f"Snapshot captured at {now_str} and sent to Telegram!",
                            }
                        else:
                            res = {"success": False, "error": data.get("description", f"HTTP {r.status_code}")}
                    except Exception as e:
                        res = {"success": False, "error": str(e)}

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))


        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        if self._handle_identities_write(parts, "DELETE", {}, []):
            return
        self.send_response(404)
        self.end_headers()


def start_unified_server(port: int = 8765, open_browser: bool = True) -> None:
    actual_port = find_free_port(port)
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("127.0.0.1", actual_port), DashboardRequestHandler)
    server.daemon_threads = True

    GLOBAL_ENGINE.start()

    url = f"http://127.0.0.1:{actual_port}"
    print("Inbound Garage", flush=True)
    print(f"Dashboard: {url}", flush=True)
    print(f"[INBOUND_SERVER_READY] port={actual_port}", flush=True)

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    shutting_down = {"done": False}

    def shutdown(*_args: object) -> None:
        if shutting_down["done"]:
            return
        shutting_down["done"] = True
        print("\nStopping Inbound Garage...", flush=True)
        try:
            GLOBAL_ENGINE.stop()
        except Exception:
            pass
        try:
            server.shutdown()
        except Exception:
            pass

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)
    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown()
    finally:
        shutdown()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inbound Garage camera hub")
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Preferred HTTP port (falls back to a free port if busy).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a system browser.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    start_unified_server(port=args.port, open_browser=not args.no_browser)
