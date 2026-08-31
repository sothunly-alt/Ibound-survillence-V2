"""Inbound Surveillance — live AI store monitor.

Serves the Stitch command-center dashboard (edge/hub.html):
- Left sidebar: live view, camera list, add-camera.
- Main feed with YOLO11 pose overlay and a draggable till ROI.
- Source / alert / integration tabs, Telegram snapshot test, occupancy telemetry.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

# TCP first (firewall-friendly). Phone RTSP often needs UDP — open_video_source
# retries with UDP if TCP produces no frames. stimeout is microseconds.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;8000000|max_delay;500000",
)
if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import cv2
import numpy as np

import requests
import yaml
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

ROOT = resource_dir()
DATA_DIR = data_dir()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import connect, has_opened_today, insert_event, upsert_minute
from occupancy import GhostCounter, GhostState, OccupancyGate, roi_to_pixels
from person import Detection, draw_detection, person_detections
from proof import save_proof
from report import build_report
from roi_edit import draw_roi_handles
from telegram_out import TelegramOut


def find_free_port(default_port: int = 8765) -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", default_port))
            return default_port
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
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
    return data


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


def protocol_from_source(source: Any) -> str:
    if isinstance(source, int) or str(source or "").strip().isdigit():
        return "webcam"
    text = str(source or "").strip().lower()
    if text.startswith("http://") or text.startswith("https://"):
        return "phone"
    return "rtsp"


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
        out.append(
            {
                "id": cid,
                "name": name or "Untitled camera",
                "source": source,
                "protocol": str(item.get("protocol") or protocol_from_source(source)),
                "vendor": str(item.get("vendor") or "generic"),
                "username": str(item.get("username") or ""),
                "rotate": item.get("rotate", "auto") if str(item.get("rotate", "")).lower() == "auto" else item.get("rotate", 0),
                "flip": str(item.get("flip") or "none"),
                "roi": parse_roi(item.get("roi")) or [0.30, 0.20, 0.40, 0.60],
            }
        )
    return out


def upsert_camera(cfg: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    cameras = _normalize_cameras(cfg.get("cameras"))
    cid = str(fields.get("id") or "").strip() or f"cam-{int(time.time() * 1000)}"
    name = str(fields.get("name") or "").strip() or "Untitled camera"
    source = fields.get("source", cfg.get("source", ""))
    if not isinstance(source, int):
        source = str(source or "")
    existing: dict[str, Any] = {}
    for cam in cameras:
        if cam["id"] == cid:
            existing = cam
            break
    roi = parse_roi(fields.get("roi"))
    if roi is None and existing:
        roi = parse_roi(existing.get("roi"))
    if roi is None:
        roi = [0.30, 0.20, 0.40, 0.60]
    rotate = fields.get("rotate", existing.get("rotate", cfg.get("rotate", "auto")))
    entry = {
        "id": cid,
        "name": name,
        "source": source,
        "protocol": str(fields.get("protocol") or existing.get("protocol") or protocol_from_source(source)),
        "vendor": str(fields.get("vendor") or existing.get("vendor") or "generic"),
        "username": str(fields.get("username") if "username" in fields else existing.get("username") or ""),
        "rotate": rotate,
        "flip": str(fields.get("flip") or existing.get("flip") or cfg.get("flip") or "none"),
        "roi": roi,
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
    return entry


def redact_source(value: Any) -> str:
    return re.sub(r"(://[^:/?#]+):([^@]+)@", r"\1:****@", str(value))


def parse_source(value: Any) -> int | str:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return text


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
    """Upright a phone HTTP stream so Auto can size the hub to the real layout.

    IP Webcam (back camera) sends the landscape sensor buffer. Android's typical
    SENSOR_ORIENTATION of 90 means 90° CW matches holding the phone upright —
    the same as the hub's CW button and main.py. Skip rotation when the JPEG is
    already portrait. Front-camera / inverted mounts still use CW / CCW / 180.
    """
    if protocol_from_source(source) != "phone":
        return 0
    if frame is None:
        return 90
    h, w = frame.shape[:2]
    if w >= h:
        return 90
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


_V4L_RELEASE_PAUSE = 0.35


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


def _open_webcam_index(idx: int) -> cv2.VideoCapture | None:
    backends: list[int] = []
    if sys.platform.startswith("linux"):
        if hasattr(cv2, "CAP_V4L2"):
            backends.append(cv2.CAP_V4L2)
    elif sys.platform == "win32":
        for name in ("CAP_DSHOW", "CAP_MSMF"):
            backend = getattr(cv2, name, None)
            if backend is not None:
                backends.append(backend)
    elif sys.platform == "darwin":
        avf = getattr(cv2, "CAP_AVFOUNDATION", None)
        if avf is not None:
            backends.append(avf)
    backends.append(cv2.CAP_ANY)

    seen: set[int] = set()
    for backend in backends:
        if backend in seen:
            continue
        seen.add(backend)
        cap = cv2.VideoCapture(idx, backend)
        if cap.isOpened():
            return cap
        cap.release()
    return None


def _split_http_auth(url: str) -> tuple[str, str, str]:
    parsed = urllib.parse.urlparse(url)
    user = urllib.parse.unquote(parsed.username or "")
    password = urllib.parse.unquote(parsed.password or "")
    host = parsed.hostname or ""
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    clean = urllib.parse.urlunparse(
        (parsed.scheme, netloc, parsed.path or "/", parsed.params, parsed.query, parsed.fragment)
    )
    return clean, user, password


def _http_stream_candidates(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    if path not in ("", "/"):
        return [url]
    out: list[str] = []
    for extra in ("/video", "/mjpegfeed", "/videofeed", "/shot.jpg"):
        out.append(
            urllib.parse.urlunparse((parsed.scheme, parsed.netloc, extra, "", parsed.query, ""))
        )
    return out


def _decode_jpeg(data: bytes):
    if not data:
        return None
    arr = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return frame


class HttpMjpegCapture:
    """MJPEG/JPEG HTTP capture with Basic and Digest login.

    IP Webcam (and similar phone apps) often require Digest auth. OpenCV/FFmpeg
    only speaks Basic, so a browser can open the same URL while VideoCapture fails.
    """

    def __init__(self, url: str, username: str = "", password: str = ""):
        self.url = url
        self.username = username
        self.password = password
        self.error: str | None = None
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "InboundSurveillance/1.0",
                "Accept": "multipart/x-mixed-replace,image/jpeg,*/*",
            }
        )
        self._resp: requests.Response | None = None
        self._buf = bytearray()
        self._mode = "mjpeg"
        self._opened = False
        err = self._connect()
        if err:
            self.error = err
            self._opened = False
        else:
            self._opened = True

    def isOpened(self) -> bool:
        return self._opened

    def set(self, *_args: Any, **_kwargs: Any) -> bool:
        return False

    def release(self) -> None:
        self._opened = False
        self._close_body()
        try:
            self._session.close()
        except Exception:
            pass

    def read(self) -> tuple[bool, Any]:
        if not self._opened:
            return False, None
        try:
            frame = self._next_frame()
            if frame is not None:
                return True, frame
        except Exception:
            self._close_body()
            if self._connect() is None:
                try:
                    frame = self._next_frame()
                    if frame is not None:
                        return True, frame
                except Exception:
                    return False, None
        return False, None

    def _auth_attempts(self) -> list[Any]:
        if not self.username:
            return [None]
        return [
            HTTPDigestAuth(self.username, self.password),
            HTTPBasicAuth(self.username, self.password),
        ]

    def _close_body(self) -> None:
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
            self._resp = None
        self._buf = bytearray()

    def _connect(self) -> str | None:
        self._close_body()
        last = f"Could not connect to camera '{redact_source(self.url)}'."
        timeout = (5, 20)
        for auth in self._auth_attempts():
            try:
                resp = self._session.get(
                    self.url,
                    auth=auth,
                    stream=True,
                    timeout=timeout,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                last = f"Could not reach '{redact_source(self.url)}': {exc}"
                continue
            if resp.status_code == 401:
                resp.close()
                last = (
                    f"Login failed for '{redact_source(self.url)}'. "
                    "This phone stream uses HTTP Digest authentication — a browser can "
                    "open it, but a plain RTSP/OpenCV connection cannot. Recheck the "
                    "username and password from IP Webcam."
                )
                continue
            if resp.status_code >= 400:
                last = f"HTTP {resp.status_code} from '{redact_source(self.url)}'."
                resp.close()
                continue
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                resp.close()
                last = (
                    f"'{redact_source(self.url)}' is the phone's web page, not a video "
                    "stream. Use http://PHONE_IP:8080/video"
                )
                continue
            self._resp = resp
            if "image/jpeg" in ctype and "multipart" not in ctype:
                self._mode = "snapshot"
            else:
                self._mode = "mjpeg"
            return None
        return last

    def _next_frame(self):
        if self._mode == "snapshot":
            return self._read_snapshot()
        return self._read_mjpeg()

    def _read_snapshot(self):
        if self._resp is not None:
            data = self._resp.content
            self._close_body()
            frame = _decode_jpeg(data)
            if frame is not None:
                return frame
        timeout = (5, 10)
        last_exc: Exception | None = None
        for auth in self._auth_attempts():
            try:
                resp = self._session.get(self.url, auth=auth, timeout=timeout)
            except requests.RequestException as exc:
                last_exc = exc
                continue
            if resp.status_code == 200:
                frame = _decode_jpeg(resp.content)
                if frame is not None:
                    return frame
            last_exc = RuntimeError(f"HTTP {resp.status_code}")
        if last_exc:
            raise last_exc
        return None

    def _read_mjpeg(self):
        if self._resp is None:
            err = self._connect()
            if err:
                raise RuntimeError(err)
        assert self._resp is not None
        while True:
            start = self._buf.find(b"\xff\xd8")
            end = self._buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
            if start >= 0 and end >= 0:
                jpeg = bytes(self._buf[start : end + 2])
                del self._buf[: end + 2]
                frame = _decode_jpeg(jpeg)
                if frame is not None:
                    return frame
                continue
            if start > 0:
                del self._buf[:start]
            if len(self._buf) > 4_000_000:
                self._buf = bytearray()
            chunk = next(self._resp.iter_content(chunk_size=16_384), b"")
            if not chunk:
                raise RuntimeError("HTTP MJPEG stream ended")
            self._buf.extend(chunk)


def _open_http_source(url: str) -> tuple[Any | None, Any, str | None]:
    clean, user, password = _split_http_auth(url)
    last_err: str | None = None
    for candidate in _http_stream_candidates(clean):
        cap = HttpMjpegCapture(candidate, user, password)
        if not cap.isOpened():
            last_err = cap.error
            cap.release()
            continue
        ok, frame = cap.read()
        if ok and frame is not None:
            return cap, frame, None
        last_err = (
            f"Logged in to '{redact_source(candidate)}' but received no JPEG frames."
        )
        cap.release()
    hint = (
        " For IP Webcam, Protocol must be Phone HTTP, URL "
        "http://PHONE_IP:8080/video, and username/password filled if the app requires them."
    )
    return None, None, (last_err or f"Could not connect to camera '{redact_source(url)}'.") + hint


def _read_first_frame(cap: cv2.VideoCapture, tries: int = 8, pause: float = 0.25):
    for _ in range(tries):
        ok, frame = cap.read()
        if ok and frame is not None:
            return frame
        time.sleep(pause)
    return None


_RTSP_FFMPEG_OPTS = (
    "rtsp_transport;tcp|stimeout;8000000|max_delay;500000|fflags;nobuffer",
    "rtsp_transport;udp|stimeout;8000000|max_delay;500000|fflags;nobuffer",
)


def _open_network_source(url: str) -> tuple[cv2.VideoCapture | None, Any, str | None]:
    """Open an RTSP or HTTP URL. RTSP tries TCP then UDP — phone apps often
    only speak UDP, while NVRs prefer TCP.
    """
    if url.lower().startswith("http://") or url.lower().startswith("https://"):
        return _open_http_source(url)

    ffmpeg = getattr(cv2, "CAP_FFMPEG", None)
    is_rtsp = url.lower().startswith("rtsp")
    prev_opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
    attempts: list[tuple[str, int | None]] = []
    if is_rtsp:
        for opts in _RTSP_FFMPEG_OPTS:
            attempts.append((opts, ffmpeg))
        attempts.append(("", None))
    else:
        attempts.append((prev_opts or "", ffmpeg))
        attempts.append(("", None))

    last_err: str | None = None
    seen: set[tuple[str, int | None]] = set()
    try:
        for opts, backend in attempts:
            key = (opts, backend)
            if key in seen:
                continue
            seen.add(key)
            if opts:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opts
            cap = (
                cv2.VideoCapture(url, backend)
                if backend is not None
                else cv2.VideoCapture(url)
            )
            if is_rtsp:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                cap.release()
                last_err = f"Could not connect to camera '{redact_source(url)}'."
                continue
            frame = _read_first_frame(cap, tries=10 if is_rtsp else 4)
            if frame is not None:
                return cap, frame, None
            cap.release()
            time.sleep(_V4L_RELEASE_PAUSE)
            last_err = (
                f"Connected to '{redact_source(url)}', but received no video frame. "
                "The stream may be offline, already in use, or using a transport "
                "this PC cannot read."
            )
    finally:
        if prev_opts is not None:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = prev_opts

    hint = ""
    if is_rtsp:
        hint = (
            " Generic RTSP on port 554 is for CCTV/NVRs, not phones. "
            "For IP Webcam, use rtsp://PHONE_IP:8080/h264_ulaw.sdp with empty "
            "username and password, or Protocol 'Phone HTTP' at "
            "http://PHONE_IP:8080/video. Both devices must be on the same Wi-Fi."
        )
    return None, None, (last_err or f"Could not connect to camera '{redact_source(url)}'.") + hint


def open_video_source(
    source: int | str,
) -> tuple[cv2.VideoCapture | None, int | str, Any, str | None]:
    """Open capture on the calling thread and read one frame.

    Webcam indices try the native backend first (V4L2, DirectShow/MSMF,
    or AVFoundation), then CAP_ANY. If the preferred node is metadata-only
    (VIDIOC_G_INPUT / index out of range), nearby indices are tried.
    """
    parsed = parse_source(source)
    if isinstance(parsed, int):
        order = [parsed] + [i for i in range(4) if i != parsed]
        last_err = f"Could not open camera index {parsed}."
        for idx in order:
            cap = _open_webcam_index(idx)
            if cap is None:
                continue
            ok, frame = cap.read()
            if ok and frame is not None:
                return cap, idx, frame, None
            cap.release()
            time.sleep(_V4L_RELEASE_PAUSE)
            last_err = (
                f"Camera index {idx} opened but produced no frames "
                "(metadata node, busy device, or index out of range)."
            )
        return None, parsed, None, last_err

    cap, frame, err = _open_network_source(str(parsed))
    if cap is None or frame is None:
        return None, parsed, None, err
    return cap, parsed, frame, None


class LiveStreamEngine:
    """Background engine that captures video, performs YOLO11 pose inference,

    tracks store occupancy, saves proofs, sends alerts, and delivers frames to the web UI.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.is_streaming = False
        self.thread: threading.Thread | None = None
        self.current_frame_jpeg: bytes | None = None
        self.new_frame_event = threading.Event()
        self.cap: cv2.VideoCapture | None = None
        self.model = None

        # Telemetry stats
        self.fps = 0.0
        self.is_occupied = False
        self.empty_elapsed = 0.0
        self.person_count = 0
        self.status_text = "STANDBY"
        self.stream_resolution = "--"
        self.error_message: str | None = None

        # Load initial config. SQLite is opened on the worker thread —
        # connections cannot be shared across threads.
        self.cfg = read_config()
        self.conn = None
        self.bot = TelegramOut(self.cfg.get("telegram_bot_token", ""), self.cfg.get("telegram_chat_id", ""))

        self._reopen_requested = False
        self._connect_generation = 0
        self._connect_event = threading.Event()
        self._connect_ok = False
        self._connect_error: str | None = None
        self._connect_wh: tuple[int, int] = (0, 0)

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.thread = threading.Thread(target=self._worker_loop, daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            self.is_streaming = False
            self._reopen_requested = True
            self._connect_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def connect_camera(self, new_cfg: dict[str, Any]) -> dict[str, Any]:
        """Ask the worker thread to open the camera. Never open VideoCapture
        from the HTTP thread — V4L2 devices are exclusive and SQLite/OpenCV
        objects are thread-affine.
        """
        t0 = time.time()
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
        camera_fields = {
            "id": cid,
            "name": cam_name,
            "protocol": payload.pop("protocol", ""),
            "vendor": payload.pop("vendor", ""),
            "username": payload.pop("username", ""),
        }
        if "roi" in payload:
            camera_fields["roi"] = payload.pop("roi")
        payload.pop("password", None)
        with self.lock:
            self.cfg.update(payload)
            if str(camera_fields.get("name") or "").strip() or str(camera_fields.get("id") or "").strip():
                camera_fields["source"] = self.cfg.get("source")
                camera_fields["rotate"] = self.cfg.get("rotate")
                camera_fields["flip"] = self.cfg.get("flip")
                upsert_camera(self.cfg, camera_fields)
            save_config(self.cfg)
            self.bot = TelegramOut(
                self.cfg.get("telegram_bot_token", ""),
                self.cfg.get("telegram_chat_id", ""),
            )
            self.error_message = None
            self._connect_ok = False
            self._connect_error = None
            self._connect_generation += 1
            self._reopen_requested = True
            self.is_streaming = True
            self.status_text = "CONNECTING"
            self._connect_event.clear()

        ok = self._connect_event.wait(timeout=30.0)
        with self.lock:
            cameras = list(self.cfg.get("cameras") or [])
            active_id = str(self.cfg.get("active_camera_id") or "")
            if self._connect_ok:
                w, h = self._connect_wh
                source = self.cfg.get("source", 0)
                roi = list(self.cfg.get("roi") or [0.30, 0.20, 0.40, 0.60])
                return {
                    "success": True,
                    "width": w,
                    "height": h,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "message": f"Successfully connected to '{redact_source(source)}' ({w}x{h})",
                    "roi": roi,
                    "rotate": self.cfg.get("rotate", 0),
                    "flip": self.cfg.get("flip", "none"),
                    "layout": "portrait" if h > w else "landscape",
                    "cameras": cameras,
                    "active_camera_id": active_id,
                }
            err = self._connect_error or (
                "Timed out opening the camera. The device may be busy, "
                "or index 0 may be a metadata node — try 1."
            )
            self.status_text = "CONNECTION FAILED"
            self.error_message = err
            self.is_streaming = False
            return {
                "success": False,
                "error": err,
                "cameras": cameras,
                "active_camera_id": active_id,
            }

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
        with self.lock:
            entry = upsert_camera(self.cfg, mapped)
            save_config(self.cfg)
            return {
                "success": True,
                "camera": entry,
                "cameras": list(self.cfg.get("cameras") or []),
                "active_camera_id": entry["id"],
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
            save_config(self.cfg)
            return {
                "success": True,
                "cameras": cameras,
                "active_camera_id": str(self.cfg.get("active_camera_id") or ""),
            }

    def set_roi(self, roi_value: Any) -> dict[str, Any]:
        parsed = parse_roi(roi_value)
        if parsed is None:
            return {"success": False, "error": "ROI must be [x, y, width, height] fractions."}
        with self.lock:
            self.cfg["roi"] = parsed
            cameras = _normalize_cameras(self.cfg.get("cameras"))
            active = str(self.cfg.get("active_camera_id") or "")
            for cam in cameras:
                if cam["id"] == active:
                    cam["roi"] = parsed
                    break
            self.cfg["cameras"] = cameras
            save_config(self.cfg)
        return {"success": True, "roi": parsed, "cameras": cameras, "active_camera_id": active}

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

    def _worker_loop(self):
        from ultralytics import YOLO

        print("[LiveStreamEngine] Loading YOLO pose model...")
        self.conn = connect(DATA_DIR / "events.db")
        weights_path = get_resource_path("yolo11n-pose.pt")
        if not weights_path.exists():
            weights_path = DATA_DIR / "yolo11n-pose.pt"
        try:
            self.model = YOLO(str(weights_path) if weights_path.exists() else "yolo11n-pose.pt")
            print("[LiveStreamEngine] Model ready")
        except Exception as e:
            self.error_message = f"Failed to load YOLO model: {e}"
            with self.lock:
                self._connect_ok = False
                self._connect_error = self.error_message
                self._connect_event.set()
            return

        proofs = DATA_DIR / "proofs"

        while self.running:
            with self.lock:
                streaming = self.is_streaming
                cfg = dict(self.cfg)
                generation = self._connect_generation
                self._reopen_requested = False

            if not streaming:
                time.sleep(0.2)
                continue

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
                cfg.get("occupy_confirm_seconds") if cfg.get("occupy_confirm_seconds") is not None else 1.0
            )
            clear = float(
                cfg.get("occupy_clear_seconds") if cfg.get("occupy_clear_seconds") is not None else 1.0
            )
            rotate_deg, flip = resolve_orient(cfg.get("rotate"), source, None, cfg.get("flip"))

            print(f"[LiveStreamEngine] Ingesting camera stream: {redact_source(source)}")
            cap, actual, first_frame, err = open_video_source(source)

            with self.lock:
                stale = generation != self._connect_generation
            if stale:
                if cap is not None:
                    cap.release()
                    time.sleep(_V4L_RELEASE_PAUSE)
                continue

            if cap is None or first_frame is None:
                with self.lock:
                    self.is_streaming = False
                    self.current_frame_jpeg = None
                    self.cap = None
                    self._connect_ok = False
                    self._connect_error = err
                    self.error_message = err
                    self.status_text = "FAILED"
                    self._connect_event.set()
                time.sleep(0.5)
                continue

            if actual != source:
                print(f"[LiveStreamEngine] Camera index {source} unavailable, using {actual}")
                with self.lock:
                    self.cfg["source"] = actual
                    cfg["source"] = actual
                    save_config(self.cfg)

            self.cap = cap
            rotate_deg, flip = resolve_orient(cfg.get("rotate"), source, first_frame, cfg.get("flip"))
            first_oriented = orient_frame(first_frame, rotate_deg, flip)
            h0, w0 = first_oriented.shape[:2]
            with self.lock:
                self._connect_wh = (w0, h0)
                self._connect_ok = True
                self._connect_error = None
                self.error_message = None
                self.status_text = "CONNECTED"
                self.stream_resolution = f"{w0}x{h0}"
                self._connect_event.set()

            ghost = GhostCounter(absent, cooldown)
            gate = OccupancyGate(confirm, clear)
            last_accepted: list[Detection] = []
            last_rejected: list[Detection] = []
            last_state = GhostState(False, 0.0, False)
            last_infer = 0.0
            frame_count = 0
            t_fps = time.time()
            pending = first_frame

            while self.running:
                with self.lock:
                    if not self.is_streaming or self._reopen_requested:
                        break
                    roi = list(self.cfg.get("roi") or [0.30, 0.20, 0.40, 0.60])
                    venue = str(self.cfg.get("venue") or "Store")
                    rotate_now = self.cfg.get("rotate")
                    flip_now = self.cfg.get("flip")

                if pending is not None:
                    frame = pending
                    pending = None
                else:
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        self.status_text = "RECONNECTING..."
                        time.sleep(0.2)
                        continue

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

                roi_px = roi_to_pixels(w, h, roi)

                if now - last_infer >= interval:
                    last_infer = now
                    try:
                        result = self.model.predict(
                            frame,
                            imgsz=imgsz,
                            conf=person_conf,
                            device=None,
                            verbose=False,
                        )[0]
                        last_accepted, last_rejected = person_detections(
                            result,
                            h,
                            conf_min=person_conf,
                            min_height_frac=min_person_height,
                            min_aspect=min_aspect,
                            min_keypoints=min_keypoints,
                            kpt_conf=kpt_conf,
                        )
                        detected = any(det.in_roi(roi_px, kpt_conf) for det in last_accepted)
                        occupied = gate.update(detected, now)
                        last_state = ghost.update(occupied, now)
                        stamp = datetime.now()

                        self.is_occupied = last_state.occupied
                        self.empty_elapsed = last_state.empty_elapsed
                        self.person_count = len(last_accepted)
                        self.status_text = (
                            "STAFF IN ROI"
                            if self.is_occupied
                            else f"EMPTY {self.empty_elapsed:.0f}/{absent:.0f}s"
                        )

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
                                path = save_proof(frame, roi_px, stamp, proofs, kind="abandoned")
                                insert_event(self.conn, "abandoned", stamp, str(path))
                                caption = (
                                    f"{venue}: front desk unattended "
                                    f"for {int(absent)}s.\n{stamp.strftime('%Y-%m-%d %H:%M:%S')}"
                                )
                                print(f"[LiveStreamEngine Alert] {path}")
                                self.bot.send_photo(path, caption)
                    except Exception as exc:
                        print(f"[LiveStreamEngine Infer Error] {exc}")

                annotated = frame.copy()
                for det in last_rejected:
                    draw_detection(
                        annotated, det, in_roi=det.in_roi(roi_px, kpt_conf), kpt_conf=kpt_conf
                    )
                for det in last_accepted:
                    draw_detection(
                        annotated, det, in_roi=det.in_roi(roi_px, kpt_conf), kpt_conf=kpt_conf
                    )

                color = (80, 200, 80) if self.is_occupied else (40, 180, 255)
                x1, y1, x2, y2 = roi_px
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                draw_roi_handles(annotated, roi_px, color)

                status_label = (
                    "STAFF IN ROI" if self.is_occupied else f"EMPTY {self.empty_elapsed:.0f}/{absent:.0f}s"
                )
                cv2.putText(
                    annotated,
                    status_label,
                    (x1, max(24, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    color,
                    2,
                    cv2.LINE_AA,
                )

                success, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if success:
                    with self.lock:
                        self.current_frame_jpeg = buffer.tobytes()
                    self.new_frame_event.set()

                time.sleep(0.015)

            if cap is not None:
                cap.release()
            self.cap = None
            time.sleep(_V4L_RELEASE_PAUSE)

        if self.conn is not None:
            self.conn.close()
            self.conn = None


GLOBAL_ENGINE = LiveStreamEngine()


HUB_HTML_PATH = get_resource_path("hub.html")


def load_hub_html() -> bytes:
    return HUB_HTML_PATH.read_text(encoding="utf-8").encode("utf-8")


class DashboardRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _cors(self) -> None:
        origin = self.headers.get("Origin") or "*"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")

    def end_headers(self):
        self._cors()
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(load_hub_html())

        elif parsed.path == "/api/config":
            cfg = read_config()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(cfg).encode("utf-8"))

        elif parsed.path == "/api/telemetry":
            data = {
                "occupied": GLOBAL_ENGINE.is_occupied,
                "empty_elapsed": GLOBAL_ENGINE.empty_elapsed,
                "person_count": GLOBAL_ENGINE.person_count,
                "fps": GLOBAL_ENGINE.fps,
                "status": GLOBAL_ENGINE.status_text,
                "resolution": GLOBAL_ENGINE.stream_resolution,
                "error": GLOBAL_ENGINE.error_message,
                "roi": list(GLOBAL_ENGINE.cfg.get("roi") or [0.30, 0.20, 0.40, 0.60]),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        elif parsed.path == "/api/stream":
            # MJPEG stream response
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.end_headers()

            try:
                while True:
                    GLOBAL_ENGINE.new_frame_event.wait(timeout=1.0)
                    GLOBAL_ENGINE.new_frame_event.clear()
                    with GLOBAL_ENGINE.lock:
                        frame_bytes = GLOBAL_ENGINE.current_frame_jpeg

                    if frame_bytes is not None:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(frame_bytes)
                        self.wfile.write(b"\r\n")
            except (ConnectionResetError, BrokenPipeError):
                pass

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}

        if parsed.path in ("/api/connect-stream", "/api/save"):
            result = GLOBAL_ENGINE.connect_camera(payload)
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

        elif parsed.path == "/api/orient":
            result = GLOBAL_ENGINE.set_orient(payload.get("rotate"), payload.get("flip"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))

        elif parsed.path == "/api/roi":
            result = GLOBAL_ENGINE.set_roi(payload.get("roi"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))

        elif parsed.path == "/api/test-telegram":
            token = str(payload.get("token") or "").strip()
            chat = str(payload.get("chat_id") or "").strip()
            venue = str(payload.get("venue") or "Demo store").strip()
            source = payload.get("source", 0)
            
            if not token or not chat:
                res = {"success": False, "error": "Bot Token and Chat ID are required."}
            else:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                proofs_dir = DATA_DIR / "proofs"
                proofs_dir.mkdir(parents=True, exist_ok=True)
                proof_path = proofs_dir / f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

                # Grab latest frame from live engine or capture one from source
                frame_bytes = None
                with GLOBAL_ENGINE.lock:
                    if GLOBAL_ENGINE.current_frame_jpeg is not None:
                        frame_bytes = GLOBAL_ENGINE.current_frame_jpeg
                
                if frame_bytes is None:
                    if GLOBAL_ENGINE.is_streaming:
                        GLOBAL_ENGINE.new_frame_event.wait(timeout=2.0)
                        with GLOBAL_ENGINE.lock:
                            frame_bytes = GLOBAL_ENGINE.current_frame_jpeg
                    else:
                        cap, _actual, f, _err = open_video_source(source)
                        ret = f is not None
                        if cap is not None:
                            if not ret:
                                ret, f = cap.read()
                            cap.release()
                            if ret and f is not None:
                                cv2.putText(f, f"{venue} | {now_str}", (16, f.shape[0] - 16),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 240, 255), 2, cv2.LINE_AA)
                                ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 85])
                                if ok:
                                    frame_bytes = buf.tobytes()

                if frame_bytes is None:
                    res = {"success": False, "error": "Could not capture camera frame. Please connect to a camera stream first."}
                else:
                    # Save local proof file
                    with proof_path.open("wb") as pf:
                        pf.write(frame_bytes)

                    status_str = "STAFF IN ROI" if GLOBAL_ENGINE.is_occupied else "EMPTY"
                    caption = (
                        f"🛡️ *Inbound Surveillance Snapshot Report*\n\n"
                        f"🏢 *Venue:* {venue}\n"
                        f"⏰ *Timestamp:* {now_str}\n"
                        f"👤 *Till Status:* {status_str}\n"
                        f"👥 *Detections:* {GLOBAL_ENGINE.person_count} Person(s)\n"
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


def start_unified_server(port: int = 8765, open_browser: bool = True) -> None:
    actual_port = find_free_port(port)
    server = ThreadingHTTPServer(("127.0.0.1", actual_port), DashboardRequestHandler)
    server.daemon_threads = True

    GLOBAL_ENGINE.start()

    url = f"http://127.0.0.1:{actual_port}"
    print("Inbound Surveillance", flush=True)
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
        print("\nStopping Inbound Surveillance...", flush=True)
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
    parser = argparse.ArgumentParser(description="Inbound Surveillance camera hub")
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
