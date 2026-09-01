"""Camera adapter contract and shared capture helpers."""

from __future__ import annotations

import re
import sys
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import cv2
import numpy as np

# Open/read timeouts stay short so a dead URL cannot stall the grabber
# (FFmpeg's default RTSP timeout is ~20s).
CAPTURE_TIMEOUT_MS = 2000
RTSP_STIMEOUT_US = 2_000_000
V4L_RELEASE_PAUSE = 0.35


class FramePacket:
    def __init__(self, frame: np.ndarray, timestamp: float, width: int, height: int):
        self.frame = frame
        self.timestamp = timestamp
        self.width = width
        self.height = height


class BaseCameraAdapter(ABC):
    """Open, read, and release a single video source.

    ``connect`` / ``read_frame`` / ``release`` run on the grabber thread only.
    Construction must be cheap and free of I/O so the HTTP thread can
    ``switch_source`` without blocking.
    """

    error: Optional[str] = None

    @abstractmethod
    def connect(self) -> bool:
        """Open camera connection with strict non-blocking timeouts."""
        pass

    @abstractmethod
    def read_frame(self) -> Optional[FramePacket]:
        """Read raw frame from source without blocking AI loop."""
        pass

    @abstractmethod
    def release(self) -> None:
        """Cleanly close and free hardware/network handles."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        pass


def redact_source(value: Any) -> str:
    return re.sub(r"(://[^:/?#]+):([^@]+)@", r"\1:****@", str(value))


def parse_source(value: Any) -> int | str:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return text


def protocol_from_source(source: Any) -> str:
    if isinstance(source, int) or str(source or "").strip().isdigit():
        return "webcam"
    text = str(source or "").strip().lower()
    if text.startswith("onvif://") or (
        (text.startswith("http://") or text.startswith("https://")) and "/onvif" in text
    ):
        return "onvif"
    if text.startswith("tapo://"):
        return "tapo"
    if text.startswith("webrtc://") or text.startswith("whep://") or text.startswith("whip://"):
        return "webrtc"
    if text.startswith("http://") or text.startswith("https://"):
        return "phone"
    return "rtsp"


def set_capture_timeouts(cap: cv2.VideoCapture) -> None:
    open_to = getattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC", None)
    read_to = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
    if open_to is not None:
        cap.set(open_to, CAPTURE_TIMEOUT_MS)
    if read_to is not None:
        cap.set(read_to, CAPTURE_TIMEOUT_MS)


def open_capture(source: Any, backend: int | None = None) -> cv2.VideoCapture:
    cap = cv2.VideoCapture()
    set_capture_timeouts(cap)
    if backend is None:
        cap.open(source)
    else:
        cap.open(source, backend)
    return cap


def read_first_frame(cap: cv2.VideoCapture, tries: int = 8, pause: float = 0.25):
    for _ in range(tries):
        ok, frame = cap.read()
        if ok and frame is not None:
            return frame
        time.sleep(pause)
    return None


def packet_from_bgr(frame: np.ndarray) -> FramePacket:
    h, w = frame.shape[:2]
    return FramePacket(frame, time.time(), int(w), int(h))


def safe_release(adapter: BaseCameraAdapter | None) -> None:
    if adapter is None:
        return
    try:
        adapter.release()
    except Exception:
        pass


def webcam_backends() -> list[int]:
    """Native backend only for the requested index — no MSMF/index probing."""
    backends: list[int] = []
    if sys.platform.startswith("linux"):
        if hasattr(cv2, "CAP_V4L2"):
            backends.append(cv2.CAP_V4L2)
    elif sys.platform == "win32":
        dshow = getattr(cv2, "CAP_DSHOW", None)
        if dshow is not None:
            backends.append(dshow)
            return backends
    elif sys.platform == "darwin":
        avf = getattr(cv2, "CAP_AVFOUNDATION", None)
        if avf is not None:
            backends.append(avf)
    backends.append(cv2.CAP_ANY)
    return backends
