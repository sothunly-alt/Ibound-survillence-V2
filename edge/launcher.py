"""Inbound Surveillance — Unified Web Control Hub & Live AI Stream Engine.

Provides an integrated, real-time web dashboard where store operators can:
- Configure RTSP/Webcam sources, rotation, flip, and store schedules on the left.
- Connect to camera on demand: verifies stream reachability before starting.
- Watch the live camera stream with YOLO11 skeleton pose detection and till ROI
  embedded directly on the right in real-time.
- Adjust till ROI zones, test Telegram alerts, and trigger shift reports.
"""

from __future__ import annotations

import base64
import json
import os
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

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;3000000")
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import cv2
import numpy as np

import requests
import yaml

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import connect, has_opened_today, insert_event, upsert_minute
from occupancy import GhostCounter, GhostState, OccupancyGate
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
    real = ROOT / "config.yaml"
    if real.exists():
        return real
    return ROOT / "config.example.yaml"


def read_config() -> dict[str, Any]:
    path = get_config_path()
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    token = os.environ.get("TELEGRAM_BOT_TOKEN", data.get("telegram_bot_token") or "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", data.get("telegram_chat_id") or "")
    data["telegram_bot_token"] = token
    data["telegram_chat_id"] = chat
    return data


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    target = ROOT / "config.yaml"
    current = {}
    if target.exists():
        try:
            with target.open("r", encoding="utf-8") as f:
                current = yaml.safe_load(f) or {}
        except Exception:
            current = {}
    elif (ROOT / "config.example.yaml").exists():
        try:
            with (ROOT / "config.example.yaml").open("r", encoding="utf-8") as f:
                current = yaml.safe_load(f) or {}
        except Exception:
            current = {}

    current.update(updates)
    with target.open("w", encoding="utf-8") as f:
        yaml.safe_dump(current, f, sort_keys=False)
    return current


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

        # Load initial config
        self.cfg = read_config()
        self.conn = connect(ROOT / "events.db")
        self.bot = TelegramOut(self.cfg.get("telegram_bot_token", ""), self.cfg.get("telegram_chat_id", ""))

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
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def connect_camera(self, new_cfg: dict[str, Any]) -> dict[str, Any]:
        """Validates camera stream first.

        If valid, updates configuration and activates stream.
        """
        source_val = parse_source(new_cfg.get("source", 0))
        rot_val = parse_rotate(new_cfg.get("rotate", 0))
        flip_val = parse_flip(new_cfg.get("flip", "none"))

        # Test opening the stream
        t0 = time.time()
        test_cap = cv2.VideoCapture(source_val)
        if isinstance(source_val, str) and str(source_val).startswith("rtsp"):
            test_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not test_cap.isOpened():
            with self.lock:
                self.is_streaming = False
                self.current_frame_jpeg = None
                self.status_text = "CONNECTION FAILED"
                self.error_message = f"Could not connect to camera '{source_val}'. Please verify IP, RTSP credentials, or device index."
            return {
                "success": False,
                "error": self.error_message,
            }

        ret, frame = test_cap.read()
        latency_ms = int((time.time() - t0) * 1000)
        test_cap.release()

        if not ret or frame is None:
            with self.lock:
                self.is_streaming = False
                self.current_frame_jpeg = None
                self.status_text = "NO VIDEO FRAME"
                self.error_message = f"Connected to '{source_val}', but received no video frame. Camera stream is offline or busy."
            return {
                "success": False,
                "error": self.error_message,
            }

        h, w = frame.shape[:2]

        # Valid! Save config and trigger background engine reload
        with self.lock:
            self.cfg.update(new_cfg)
            save_config(self.cfg)
            self.bot = TelegramOut(self.cfg.get("telegram_bot_token", ""), self.cfg.get("telegram_chat_id", ""))
            self.is_streaming = True
            self.error_message = None
            self.status_text = "CONNECTED"
            self.stream_resolution = f"{w}x{h}"

            # Release previous cap to force worker to open the new stream
            if self.cap:
                self.cap.release()
                self.cap = None

        return {
            "success": True,
            "width": w,
            "height": h,
            "latency_ms": latency_ms,
            "message": f"Successfully connected to '{source_val}' ({w}x{h}, {latency_ms}ms)",
        }

    def _worker_loop(self):
        from ultralytics import YOLO

        weights_path = ROOT / "yolo11n-pose.pt"
        try:
            self.model = YOLO(str(weights_path))
        except Exception as e:
            self.error_message = f"Failed to load YOLO model: {e}"
            return

        proofs = ROOT / "proofs"

        while self.running:
            with self.lock:
                streaming = self.is_streaming
                cfg = dict(self.cfg)

            if not streaming:
                time.sleep(0.2)
                continue

            source = parse_source(cfg.get("source", 0))
            roi = list(cfg.get("roi") or [0.30, 0.20, 0.40, 0.60])
            absent = float(cfg.get("absent_seconds") or 10)
            cooldown = float(cfg.get("cooldown_seconds") or 30)
            detect_fps = max(0.5, float(cfg.get("detect_fps") or 8.0))
            interval = 1.0 / detect_fps
            person_conf = float(cfg.get("person_conf") if cfg.get("person_conf") is not None else 0.35)
            min_person_height = float(cfg.get("min_person_height") if cfg.get("min_person_height") is not None else 0.12)
            min_aspect = float(cfg.get("min_aspect") if cfg.get("min_aspect") is not None else 1.1)
            min_keypoints = int(cfg.get("min_keypoints") if cfg.get("min_keypoints") is not None else 4)
            kpt_conf = float(cfg.get("kpt_conf") if cfg.get("kpt_conf") is not None else 0.4)
            imgsz = max(32, int(cfg.get("imgsz") or 640) // 32 * 32)
            confirm = float(cfg.get("occupy_confirm_seconds") if cfg.get("occupy_confirm_seconds") is not None else 1.0)
            clear = float(cfg.get("occupy_clear_seconds") if cfg.get("occupy_clear_seconds") is not None else 1.0)
            rotate_deg = parse_rotate(cfg.get("rotate"))
            flip = parse_flip(cfg.get("flip"))

            # Open video capture
            print(f"[LiveStreamEngine] Ingesting camera stream: {source}")
            cap = cv2.VideoCapture(source)
            if isinstance(source, str) and str(source).startswith("rtsp"):
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                with self.lock:
                    self.is_streaming = False
                    self.current_frame_jpeg = None
                    self.error_message = f"Failed to open video source: {source}"
                    self.status_text = "FAILED"
                time.sleep(1.0)
                continue

            self.cap = cap
            self.error_message = None
            ghost = GhostCounter(absent, cooldown)
            gate = OccupancyGate(confirm, clear)
            last_accepted: list[Detection] = []
            last_rejected: list[Detection] = []
            last_state = GhostState(False, 0.0, False)
            last_infer = 0.0
            frame_count = 0
            t_fps = time.time()

            while self.running and self.is_streaming and self.cap is cap:
                ret, frame = cap.read()
                if not ret or frame is None:
                    self.status_text = "RECONNECTING..."
                    time.sleep(0.2)
                    continue

                frame = orient_frame(frame, rotate_deg, flip)
                h, w = frame.shape[:2]
                self.stream_resolution = f"{w}x{h}"

                now = time.time()
                frame_count += 1
                if now - t_fps >= 1.0:
                    self.fps = frame_count / (now - t_fps)
                    frame_count = 0
                    t_fps = now

                # Calculate ROI pixel rectangle
                rx, ry, rw, rh = roi
                roi_px = (int(rx * w), int(ry * h), int((rx + rw) * w), int((ry + rh) * h))

                # YOLO Inference loop
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
                        self.status_text = "STAFF IN ROI" if self.is_occupied else f"EMPTY {self.empty_elapsed:.0f}/{absent:.0f}s"

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
                                f"{cfg.get('venue', 'Store')}: front desk unattended "
                                f"for {int(absent)}s.\n{stamp.strftime('%Y-%m-%d %H:%M:%S')}"
                            )
                            print(f"[LiveStreamEngine Alert] {path}")
                            self.bot.send_photo(path, caption)
                    except Exception as exc:
                        print(f"[LiveStreamEngine Infer Error] {exc}")

                # Draw annotations for web stream
                annotated = frame.copy()
                for det in last_rejected:
                    draw_detection(annotated, det, in_roi=det.in_roi(roi_px, kpt_conf), kpt_conf=kpt_conf)
                for det in last_accepted:
                    draw_detection(annotated, det, in_roi=det.in_roi(roi_px, kpt_conf), kpt_conf=kpt_conf)

                # Draw ROI rectangle & handles
                color = (80, 200, 80) if self.is_occupied else (40, 180, 255)
                x1, y1, x2, y2 = roi_px
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                draw_roi_handles(annotated, roi_px, color)

                # Overlay status text banner
                status_label = "STAFF IN ROI" if self.is_occupied else f"EMPTY {self.empty_elapsed:.0f}/{absent:.0f}s"
                cv2.putText(annotated, status_label, (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)

                # Encode to JPEG for web streaming
                success, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if success:
                    with self.lock:
                        self.current_frame_jpeg = buffer.tobytes()
                    self.new_frame_event.set()

                time.sleep(0.015)

            if cap:
                cap.release()
                self.cap = None

        if self.conn:
            self.conn.close()


GLOBAL_ENGINE = LiveStreamEngine()


HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Inbound Surveillance — Live AI Store Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #05080E;
      --card-bg: rgba(13, 19, 32, 0.85);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-hover: rgba(255, 255, 255, 0.12);
      --accent: #00F0FF;
      --accent-emerald: #10B981;
      --accent-amber: #F59E0B;
      --accent-rose: #F43F5E;
      --text: #F8FAFC;
      --text-muted: #94A3B8;
      --text-dim: #64748B;
      --input-bg: rgba(6, 10, 18, 0.95);
      --input-border: rgba(255, 255, 255, 0.12);
      --radius-lg: 14px;
      --radius-md: 8px;
      --radius-sm: 5px;
      --font-display: 'Outfit', sans-serif;
      --font-body: 'Plus Jakarta Sans', sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      background-color: var(--bg);
      background-image: 
        radial-gradient(circle at 10% 10%, rgba(0, 240, 255, 0.06) 0%, transparent 40%),
        radial-gradient(circle at 90% 90%, rgba(16, 185, 129, 0.04) 0%, transparent 40%),
        linear-gradient(180deg, #05080E 0%, #090E18 100%);
      color: var(--text);
      font-family: var(--font-body);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 16px 20px;
    }

    .container {
      width: 100%;
      max-width: 1240px;
    }

    /* Top Bar */
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--card-border);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .brand__logo {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: linear-gradient(135deg, rgba(0, 240, 255, 0.25), rgba(16, 185, 129, 0.25));
      border: 1px solid rgba(0, 240, 255, 0.4);
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 0 16px rgba(0, 240, 255, 0.2);
    }

    .brand__logo svg {
      width: 20px;
      height: 20px;
      stroke: var(--accent);
    }

    .brand__title {
      font-family: var(--font-display);
      font-size: 19px;
      font-weight: 700;
      letter-spacing: -0.3px;
      color: #fff;
    }

    .brand__subtitle {
      font-size: 12px;
      color: var(--text-muted);
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .badge-status {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 12px;
      border-radius: 999px;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: var(--accent-emerald);
      font-size: 11px;
      font-weight: 600;
      font-family: var(--font-mono);
    }

    .badge-status__dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background-color: var(--accent-emerald);
      box-shadow: 0 0 8px var(--accent-emerald);
      animation: pulse 2s infinite ease-in-out;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.5; transform: scale(0.85); }
    }

    /* Main Two-Column Layout */
    .main-grid {
      display: grid;
      grid-template-columns: 440px 1fr;
      gap: 18px;
    }

    @media (max-width: 1024px) {
      .main-grid {
        grid-template-columns: 1fr;
      }
    }

    /* Cards */
    .card {
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-lg);
      padding: 18px 20px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    }

    .card__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }

    .card__title {
      font-family: var(--font-display);
      font-size: 15px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
      color: #fff;
    }

    .card__step {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 20px;
      height: 20px;
      border-radius: 5px;
      background: rgba(0, 240, 255, 0.15);
      border: 1px solid rgba(0, 240, 255, 0.3);
      color: var(--accent);
      font-size: 11px;
      font-weight: 700;
      font-family: var(--font-mono);
    }

    /* Preset Tabs */
    .preset-tabs {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
      margin-bottom: 12px;
    }

    .preset-btn {
      background: var(--input-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-md);
      padding: 8px 6px;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 12px;
      font-weight: 600;
      font-family: var(--font-body);
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 3px;
      transition: all 0.2s ease;
    }

    .preset-btn:hover {
      border-color: var(--card-hover);
      color: var(--text);
    }

    .preset-btn.active {
      background: linear-gradient(135deg, rgba(0, 240, 255, 0.15), rgba(16, 185, 129, 0.1));
      border-color: var(--accent);
      color: #fff;
      box-shadow: 0 0 10px rgba(0, 240, 255, 0.15);
    }

    .preset-btn svg {
      width: 16px;
      height: 16px;
    }

    /* Forms */
    .form-group {
      margin-bottom: 12px;
    }

    .form-label {
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 5px;
    }

    .form-label span.hint {
      color: var(--text-dim);
      font-weight: 400;
      font-size: 11px;
    }

    .form-control {
      width: 100%;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: var(--radius-md);
      padding: 8px 11px;
      color: #fff;
      font-family: var(--font-body);
      font-size: 13px;
      transition: all 0.2s ease;
    }

    .form-control:focus {
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(0, 240, 255, 0.15);
    }

    .form-control.mono {
      font-family: var(--font-mono);
      font-size: 12px;
    }

    .brand-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin-top: 6px;
    }

    .brand-pill {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 10px;
      font-weight: 600;
      color: var(--text-muted);
      cursor: pointer;
    }

    .brand-pill:hover {
      background: rgba(0, 240, 255, 0.12);
      border-color: var(--accent);
      color: var(--accent);
    }

    .form-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }

    /* Buttons */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 9px 14px;
      border-radius: var(--radius-md);
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      border: none;
      font-family: var(--font-body);
    }

    .btn--secondary {
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.12);
      color: var(--text);
      width: 100%;
    }

    .btn--secondary:hover {
      background: rgba(255, 255, 255, 0.1);
      border-color: rgba(255, 255, 255, 0.2);
    }

    .btn--sm {
      padding: 6px 10px;
      font-size: 11px;
    }

    .btn--primary-glowing {
      width: 100%;
      padding: 13px 18px;
      border-radius: var(--radius-lg);
      background: linear-gradient(135deg, #00F0FF 0%, #0099FF 50%, #10B981 100%);
      color: #030811;
      font-family: var(--font-display);
      font-size: 15px;
      font-weight: 800;
      letter-spacing: -0.2px;
      box-shadow: 0 0 20px rgba(0, 240, 255, 0.35), 0 4px 14px rgba(0, 0, 0, 0.4);
      cursor: pointer;
    }

    .btn--primary-glowing:hover {
      transform: translateY(-1px);
      box-shadow: 0 0 30px rgba(0, 240, 255, 0.55), 0 6px 18px rgba(0, 0, 0, 0.5);
    }

    /* Live Video Monitor Frame on Right Side */
    .stream-card {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 520px;
      background: #030509;
      border: 1px solid rgba(0, 240, 255, 0.25);
      box-shadow: 0 0 30px rgba(0, 240, 255, 0.1), 0 10px 30px rgba(0, 0, 0, 0.5);
    }

    .stream-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 12px;
      margin-bottom: 12px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }

    .stream-badges {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .badge-pill {
      font-family: var(--font-mono);
      font-size: 11px;
      font-weight: 600;
      padding: 3px 8px;
      border-radius: var(--radius-sm);
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.1);
      color: var(--text-muted);
    }

    .badge-pill.active {
      background: rgba(16, 185, 129, 0.15);
      border-color: rgba(16, 185, 129, 0.4);
      color: var(--accent-emerald);
    }

    .badge-pill.warning {
      background: rgba(245, 158, 11, 0.15);
      border-color: rgba(245, 158, 11, 0.4);
      color: var(--accent-amber);
    }

    .badge-pill.danger {
      background: rgba(244, 63, 94, 0.15);
      border-color: rgba(244, 63, 94, 0.4);
      color: var(--accent-rose);
    }

    .video-viewport {
      flex: 1;
      width: 100%;
      background: #020306;
      border-radius: var(--radius-md);
      border: 1px solid rgba(255, 255, 255, 0.08);
      position: relative;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 380px;
    }

    .video-viewport img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: #000;
    }

    .stream-standby-placeholder {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
      color: var(--text-dim);
      font-size: 13px;
      text-align: center;
      padding: 24px;
    }

    .stream-standby-placeholder svg {
      width: 44px;
      height: 44px;
      stroke: var(--text-dim);
    }

    .stream-overlay-top {
      position: absolute;
      top: 12px;
      left: 12px;
      display: flex;
      gap: 8px;
      z-index: 10;
    }

    .stream-overlay-bottom {
      position: absolute;
      bottom: 12px;
      left: 12px;
      right: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 6px 12px;
      border-radius: var(--radius-sm);
      background: rgba(3, 8, 17, 0.8);
      backdrop-filter: blur(8px);
      border: 1px solid rgba(255, 255, 255, 0.1);
      font-size: 11px;
      font-family: var(--font-mono);
      color: var(--text-muted);
    }

    .stream-telemetry {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      margin-top: 12px;
    }

    .telemetry-box {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: var(--radius-md);
      padding: 8px 10px;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .telemetry-box .label {
      font-size: 10px;
      color: var(--text-dim);
      text-transform: uppercase;
      font-weight: 600;
    }

    .telemetry-box .val {
      font-family: var(--font-mono);
      font-size: 13px;
      font-weight: 700;
      color: #fff;
    }

    /* Feedback Banner */
    .feedback-banner {
      padding: 8px 12px;
      border-radius: var(--radius-md);
      font-size: 12px;
      margin-top: 8px;
      display: none;
      align-items: center;
      gap: 8px;
    }

    .feedback-banner.success {
      display: flex;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #A7F3D0;
    }

    .feedback-banner.error {
      display: flex;
      background: rgba(244, 63, 94, 0.12);
      border: 1px solid rgba(244, 63, 94, 0.3);
      color: #FECDD3;
    }

    .slider-container {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    input[type=range] {
      flex: 1;
      accent-color: var(--accent);
      cursor: pointer;
    }

    .spinner {
      width: 14px;
      height: 14px;
      border: 2px solid rgba(255, 255, 255, 0.2);
      border-top-color: currentColor;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }
  </style>
</head>
<body>

  <div class="container">
    <!-- Header -->
    <header class="header">
      <div class="brand">
        <div class="brand__logo">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
            <circle cx="12" cy="13" r="4"></circle>
          </svg>
        </div>
        <div>
          <h1 class="brand__title">Inbound Surveillance</h1>
          <p class="brand__subtitle">Live AI Edge Vision Platform</p>
        </div>
      </div>
      <div class="header-actions">
        <div class="badge-status">
          <span class="badge-status__dot"></span>
          <span>YOLO11 POSE ENGINE ACTIVE</span>
        </div>
      </div>
    </header>

    <!-- Main Grid -->
    <div class="main-grid">

      <!-- Left Column: Controls & Settings -->
      <div style="display: flex; flex-direction: column; gap: 14px;">

        <!-- Step 1: Camera Source -->
        <div class="card">
          <div class="card__header">
            <h2 class="card__title">
              <span class="card__step">1</span>
              <span>Camera Stream Source</span>
            </h2>
          </div>

          <div class="preset-tabs">
            <button type="button" class="preset-btn active" id="tab-webcam" onclick="selectSourceTab('webcam')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
              <span>Laptop Webcam (0)</span>
            </button>
            <button type="button" class="preset-btn" id="tab-rtsp" onclick="selectSourceTab('rtsp')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
              <span>RTSP IP Camera</span>
            </button>
            <button type="button" class="preset-btn" id="tab-phone" onclick="selectSourceTab('phone')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>
              <span>Phone HTTP</span>
            </button>
          </div>

          <div class="form-group">
            <label class="form-label" for="source-input">
              <span>Stream URL / Camera Device</span>
              <span class="hint" id="source-hint">Device 0 (Built-in Webcam)</span>
            </label>
            <input type="text" id="source-input" class="form-control mono" value="0" placeholder="0 or rtsp://admin:pass@ip:554/stream" />
            
            <div class="brand-pills" id="brand-helpers" style="display: none;">
              <span style="font-size: 10px; color: var(--text-dim); align-self: center;">Templates:</span>
              <button type="button" class="brand-pill" onclick="applyPreset('dahua')">Dahua</button>
              <button type="button" class="brand-pill" onclick="applyPreset('hikvision')">Hikvision</button>
              <button type="button" class="brand-pill" onclick="applyPreset('uniview')">Uniview</button>
              <button type="button" class="brand-pill" onclick="applyPreset('reolink')">Reolink</button>
              <button type="button" class="brand-pill" onclick="applyPreset('phone')">IP Webcam</button>
            </div>
          </div>

          <div class="form-row">
            <div class="form-group" style="margin-bottom: 0;">
              <label class="form-label" for="rotate-input">Rotation</label>
              <select id="rotate-input" class="form-control">
                <option value="0">0° (Standard)</option>
                <option value="90">90° Clockwise</option>
                <option value="180">180° Inverted</option>
                <option value="270">270° Counter-Clockwise</option>
              </select>
            </div>
            <div class="form-group" style="margin-bottom: 0;">
              <label class="form-label" for="flip-input">Mirror / Flip</label>
              <select id="flip-input" class="form-control">
                <option value="none">None (Standard)</option>
                <option value="h">Horizontal (Mirror)</option>
                <option value="v">Vertical</option>
              </select>
            </div>
          </div>

          <button type="button" class="btn btn--primary-glowing" id="btn-connect" onclick="connectAndStreamCamera()" style="margin-top: 12px;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            <span>CONNECT & STREAM CAMERA ON RIGHT</span>
          </button>
          
          <div id="camera-feedback" class="feedback-banner"></div>
        </div>

        <!-- Step 2: Store Details & Rules -->
        <div class="card">
          <div class="card__header">
            <h2 class="card__title">
              <span class="card__step">2</span>
              <span>Store Rules & Alert Threshold</span>
            </h2>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label class="form-label" for="venue-input">Venue Name</label>
              <input type="text" id="venue-input" class="form-control" value="Demo store" placeholder="e.g. Downtown Store" />
            </div>
            <div class="form-group">
              <label class="form-label" for="open-time-input">Opening Time</label>
              <input type="time" id="open-time-input" class="form-control" value="08:00" />
            </div>
          </div>

          <div class="form-group" style="margin-bottom: 0;">
            <div class="form-label">
              <span>Absent Till Alert Threshold</span>
              <span class="hint" id="absent-val-label">10 seconds</span>
            </div>
            <div class="slider-container">
              <input type="range" id="absent-slider" min="5" max="300" step="5" value="10" oninput="updateAbsentLabel(this.value)">
            </div>
            <p style="font-size: 10px; color: var(--text-dim); margin-top: 4px;">~10s for fast live demos; ~180s for real cashier shifts.</p>
          </div>
        </div>

        <!-- Step 3: Telegram Alert Bot -->
        <div class="card">
          <div class="card__header">
            <h2 class="card__title">
              <span class="card__step">3</span>
              <span>Telegram Alert Bot (Optional)</span>
            </h2>
          </div>

          <div class="form-group">
            <label class="form-label" for="tg-token-input">Bot Token</label>
            <input type="password" id="tg-token-input" class="form-control mono" placeholder="Leave blank if not using Telegram" />
          </div>

          <div class="form-group">
            <label class="form-label" for="tg-chat-input">Chat ID / Channel ID</label>
            <input type="text" id="tg-chat-input" class="form-control mono" placeholder="e.g. -1001234567890" />
          </div>

          <button type="button" class="btn btn--secondary btn--sm" id="btn-test-tg" onclick="testTelegram()">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>
            <span>📸 Capture & Send Live Snapshot to Telegram</span>
          </button>

          <div id="tg-feedback" class="feedback-banner"></div>
        </div>

      </div>

      <!-- Right Column: Live Stream & AI Telemetry -->
      <div style="display: flex; flex-direction: column;">
        <div class="card stream-card">
          <div class="stream-header">
            <div class="card__title">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="var(--accent)"/></svg>
              <span>Live AI Surveillance Feed</span>
            </div>
            <div class="stream-badges">
              <span id="badge-status-pill" class="badge-pill">STANDBY</span>
              <span id="badge-res-pill" class="badge-pill">--</span>
            </div>
          </div>

          <!-- Video Viewport Displaying Direct MJPEG Stream -->
          <div class="video-viewport" id="video-viewport">
            <div class="stream-standby-placeholder" id="stream-placeholder">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/>
              </svg>
              <span>Input your camera source or RTSP stream on the left and click<br><strong style="color: var(--accent);">"CONNECT & STREAM CAMERA ON RIGHT"</strong></span>
            </div>

            <img id="live-camera-feed" style="display: none;" alt="Live Camera AI Stream" onerror="handleStreamError()" />
            
            <div class="stream-overlay-top" id="stream-overlay-top" style="display: none;">
              <span id="overlay-occupancy" class="badge-pill active" style="background: rgba(16, 185, 129, 0.3);">STAFF IN ROI</span>
            </div>

            <div class="stream-overlay-bottom" id="stream-overlay-bottom" style="display: none;">
              <span>Model: YOLO11n-pose</span>
              <span id="overlay-fps">FPS: --</span>
            </div>
          </div>

          <!-- Telemetry Row -->
          <div class="stream-telemetry">
            <div class="telemetry-box">
              <span class="label">Till Status</span>
              <span class="val" id="telem-status">STANDBY</span>
            </div>
            <div class="telemetry-box">
              <span class="label">Empty Elapsed</span>
              <span class="val" id="telem-elapsed">--</span>
            </div>
            <div class="telemetry-box">
              <span class="label">Detections</span>
              <span class="val" id="telem-people">--</span>
            </div>
            <div class="telemetry-box">
              <span class="label">Stream FPS</span>
              <span class="val" id="telem-fps" style="color: var(--accent);">-- FPS</span>
            </div>
          </div>

        </div>
      </div>

    </div>
  </div>

  <script>
    let currentConfig = {};
    let isStreamActive = false;

    function selectSourceTab(tab) {
      document.getElementById('tab-webcam').classList.toggle('active', tab === 'webcam');
      document.getElementById('tab-rtsp').classList.toggle('active', tab === 'rtsp');
      document.getElementById('tab-phone').classList.toggle('active', tab === 'phone');

      const sourceInput = document.getElementById('source-input');
      const hint = document.getElementById('source-hint');
      const brandHelpers = document.getElementById('brand-helpers');

      if (tab === 'webcam') {
        sourceInput.value = '0';
        hint.innerText = 'Device 0 (Built-in Webcam)';
        brandHelpers.style.display = 'none';
      } else if (tab === 'rtsp') {
        if (sourceInput.value === '0' || sourceInput.value.startsWith('http://')) {
          sourceInput.value = 'rtsp://admin:password@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0';
        }
        hint.innerText = 'RTSP Stream URL from NVR or IP Camera';
        brandHelpers.style.display = 'flex';
      } else if (tab === 'phone') {
        sourceInput.value = 'http://192.168.1.50:8080/video';
        hint.innerText = 'IP Webcam HTTP Video URL';
        brandHelpers.style.display = 'none';
      }
    }

    function applyPreset(brand) {
      const sourceInput = document.getElementById('source-input');
      if (brand === 'dahua') {
        sourceInput.value = 'rtsp://admin:PASSWORD@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0';
      } else if (brand === 'hikvision') {
        sourceInput.value = 'rtsp://admin:PASSWORD@192.168.1.64:554/Streaming/Channels/101';
      } else if (brand === 'uniview') {
        sourceInput.value = 'rtsp://admin:PASSWORD@192.168.1.13:554/unicast/c1/s0/live';
      } else if (brand === 'reolink') {
        sourceInput.value = 'rtsp://admin:PASSWORD@192.168.1.100:554/h264Preview_01_main';
      } else if (brand === 'phone') {
        sourceInput.value = 'http://192.168.1.50:8080/video';
      }
    }

    function updateAbsentLabel(val) {
      document.getElementById('absent-val-label').innerText = val + ' seconds';
    }

    async function loadInitialConfig() {
      try {
        const res = await fetch('/api/config');
        if (!res.ok) return;
        currentConfig = await res.json();

        if (currentConfig.source !== undefined) {
          const srcStr = String(currentConfig.source);
          document.getElementById('source-input').value = srcStr;
          if (srcStr === '0' || srcStr === '1') {
            selectSourceTab('webcam');
          } else if (srcStr.startsWith('http://') || srcStr.startsWith('https://')) {
            selectSourceTab('phone');
          } else {
            selectSourceTab('rtsp');
          }
        }

        if (currentConfig.venue) document.getElementById('venue-input').value = currentConfig.venue;
        if (currentConfig.open_time) document.getElementById('open-time-input').value = currentConfig.open_time;
        if (currentConfig.absent_seconds) {
          document.getElementById('absent-slider').value = currentConfig.absent_seconds;
          updateAbsentLabel(currentConfig.absent_seconds);
        }
        if (currentConfig.rotate !== undefined) document.getElementById('rotate-input').value = currentConfig.rotate;
        if (currentConfig.flip !== undefined) document.getElementById('flip-input').value = currentConfig.flip;
        if (currentConfig.telegram_bot_token) document.getElementById('tg-token-input').value = currentConfig.telegram_bot_token;
        if (currentConfig.telegram_chat_id) document.getElementById('tg-chat-input').value = currentConfig.telegram_chat_id;
      } catch (err) {
        console.error('Failed to load config:', err);
      }
    }

    async function connectAndStreamCamera() {
      const feedback = document.getElementById('camera-feedback');
      const btn = document.getElementById('btn-connect');
      const placeholder = document.getElementById('stream-placeholder');
      const img = document.getElementById('live-camera-feed');
      const overlayTop = document.getElementById('stream-overlay-top');
      const overlayBottom = document.getElementById('stream-overlay-bottom');
      const badgePill = document.getElementById('badge-status-pill');

      const source = document.getElementById('source-input').value;
      const venue = document.getElementById('venue-input').value;
      const open_time = document.getElementById('open-time-input').value;
      const absent_seconds = Number(document.getElementById('absent-slider').value);
      const rotate = document.getElementById('rotate-input').value;
      const flip = document.getElementById('flip-input').value;
      const telegram_bot_token = document.getElementById('tg-token-input').value;
      const telegram_chat_id = document.getElementById('tg-chat-input').value;

      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> <span>Verifying Stream & Connecting...</span>';
      feedback.style.display = 'none';

      try {
        const res = await fetch('/api/connect-stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source, venue, open_time, absent_seconds, rotate, flip, telegram_bot_token, telegram_chat_id
          })
        });
        const data = await res.json();

        if (data.success) {
          isStreamActive = true;
          feedback.className = 'feedback-banner success';
          feedback.innerHTML = '✅ ' + data.message;

          // Start image stream
          img.src = '/api/stream?t=' + Date.now();
          img.style.display = 'block';
          placeholder.style.display = 'none';
          overlayTop.style.display = 'flex';
          overlayBottom.style.display = 'flex';
          badgePill.className = 'badge-pill active';
          badgePill.innerText = 'LIVE ' + data.width + 'x' + data.height;
          document.getElementById('badge-res-pill').innerText = data.width + 'x' + data.height;
        } else {
          isStreamActive = false;
          feedback.className = 'feedback-banner error';
          feedback.innerHTML = '❌ ' + (data.error || 'Connection failed.');
          
          // Show error state on stream frame
          img.style.display = 'none';
          placeholder.style.display = 'flex';
          placeholder.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" stroke="var(--accent-rose)" stroke-width="1.5">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="15" y1="9" x2="9" y2="15"></line>
              <line x1="9" y1="9" x2="15" y2="15"></line>
            </svg>
            <span style="color: #FECDD3;"><strong>Stream Connection Failed</strong><br>${data.error || 'Please check IP address or credentials'}</span>
          `;
          overlayTop.style.display = 'none';
          overlayBottom.style.display = 'none';
          badgePill.className = 'badge-pill danger';
          badgePill.innerText = 'OFFLINE';
          document.getElementById('telem-status').innerText = 'ERROR';
          document.getElementById('telem-status').style.color = 'var(--accent-rose)';
        }
      } catch (err) {
        feedback.className = 'feedback-banner error';
        feedback.innerHTML = '❌ Connection Error: ' + err.message;
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg> <span>CONNECT & STREAM CAMERA ON RIGHT</span>';
      }
    }

    function handleStreamError() {
      if (isStreamActive) {
        const badgePill = document.getElementById('badge-status-pill');
        badgePill.className = 'badge-pill warning';
        badgePill.innerText = 'RECONNECTING';
      }
    }

    async function pollTelemetry() {
      if (!isStreamActive) return;
      try {
        const res = await fetch('/api/telemetry');
        if (!res.ok) return;
        const data = await res.json();

        if (data.status === "FAILED") {
          document.getElementById('telem-status').innerText = 'STREAM ERROR';
          document.getElementById('telem-status').style.color = 'var(--accent-rose)';
          return;
        }

        // Update telemetry boxes
        document.getElementById('telem-status').innerText = data.occupied ? 'STAFF IN ROI' : 'EMPTY';
        document.getElementById('telem-status').style.color = data.occupied ? 'var(--accent-emerald)' : 'var(--accent-amber)';
        document.getElementById('telem-elapsed').innerText = data.empty_elapsed.toFixed(0) + 's';
        document.getElementById('telem-people').innerText = data.person_count + (data.person_count === 1 ? ' Person' : ' Persons');
        document.getElementById('telem-fps').innerText = data.fps.toFixed(1) + ' FPS';

        // Update badges
        document.getElementById('overlay-fps').innerText = 'FPS: ' + data.fps.toFixed(1);
        
        const overlayOcc = document.getElementById('overlay-occupancy');
        if (data.occupied) {
          overlayOcc.style.background = 'rgba(16, 185, 129, 0.35)';
          overlayOcc.innerText = 'STAFF IN ROI';
        } else {
          overlayOcc.style.background = 'rgba(245, 158, 11, 0.35)';
          overlayOcc.innerText = 'TILL EMPTY (' + data.empty_elapsed.toFixed(0) + 's)';
        }
      } catch (err) {
        // Ignore poll glitches
      }
    }

    async function testTelegram() {
      const btn = document.getElementById('btn-test-tg');
      const feedback = document.getElementById('tg-feedback');
      const token = document.getElementById('tg-token-input').value;
      const chat_id = document.getElementById('tg-chat-input').value;
      const venue = document.getElementById('venue-input').value;
      const source = document.getElementById('source-input').value;

      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> <span>Capturing Frame & Dispatching...</span>';
      feedback.style.display = 'none';

      try {
        const res = await fetch('/api/test-telegram', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, chat_id, venue, source })
        });
        const data = await res.json();

        if (data.success) {
          feedback.className = 'feedback-banner success';
          feedback.innerHTML = '✅ ' + data.message;
        } else {
          feedback.className = 'feedback-banner error';
          feedback.innerHTML = '❌ ' + (data.error || 'Failed to deliver Telegram photo.');
        }
      } catch (err) {
        feedback.className = 'feedback-banner error';
        feedback.innerHTML = '❌ Error: ' + err.message;
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg> <span>📸 Capture & Send Live Snapshot to Telegram</span>';
      }
    }


    window.addEventListener('DOMContentLoaded', () => {
      loadInitialConfig();
      setInterval(pollTelemetry, 600);
    });
  </script>
</body>
</html>
"""


class DashboardRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode("utf-8"))

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

        elif parsed.path == "/api/test-telegram":
            token = str(payload.get("token") or "").strip()
            chat = str(payload.get("chat_id") or "").strip()
            venue = str(payload.get("venue") or "Demo store").strip()
            source = payload.get("source", 0)
            
            if not token or not chat:
                res = {"success": False, "error": "Bot Token and Chat ID are required."}
            else:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                proofs_dir = ROOT / "proofs"
                proofs_dir.mkdir(parents=True, exist_ok=True)
                proof_path = proofs_dir / f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

                # Grab latest frame from live engine or capture one from source
                frame_bytes = None
                with GLOBAL_ENGINE.lock:
                    if GLOBAL_ENGINE.current_frame_jpeg is not None:
                        frame_bytes = GLOBAL_ENGINE.current_frame_jpeg
                
                if frame_bytes is None:
                    # Capture one frame directly from camera source
                    src = parse_source(source)
                    cap = cv2.VideoCapture(src)
                    if cap.isOpened():
                        ret, f = cap.read()
                        cap.release()
                        if ret and f is not None:
                            # Overlay timestamp on captured frame
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


def start_unified_server(port: int = 8765):
    actual_port = find_free_port(port)
    server = ThreadingHTTPServer(("127.0.0.1", actual_port), DashboardRequestHandler)

    # Start live video streaming engine worker thread
    GLOBAL_ENGINE.start()

    url = f"http://127.0.0.1:{actual_port}"
    print(f"\n==================================================================")
    print(f"🛡️  INBOUND SURVEILLANCE — LIVE AI STORE MONITOR & DASHBOARD")
    print(f"👉 Live Web Dashboard: {url}")
    print(f"==================================================================\n")

    # Open browser automatically
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Inbound Surveillance...")
        GLOBAL_ENGINE.stop()
        server.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    start_unified_server()
