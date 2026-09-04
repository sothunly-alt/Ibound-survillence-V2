"""Tests for VideoFileAdapter and native video streaming endpoints."""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import create_adapter, create_direct_adapter, ingest_kind
from adapters.base import protocol_from_source, unwrap_local_video_source
from adapters.video_file import VideoFileAdapter, resolve_video_path
from capture import AsyncFrameGrabber


SAMPLE_VIDEO = "tools/virtual-camera/videos/sample_garage_demo.mp4"


def test_path_resolution() -> None:
    # 1. By relative path
    resolved = resolve_video_path(SAMPLE_VIDEO)
    assert resolved.is_file(), f"Failed relative path: {resolved}"

    # 2. By filename only
    resolved_name = resolve_video_path("sample_garage_demo.mp4")
    assert resolved_name.is_file(), f"Failed filename-only path: {resolved_name}"

    # 3. By absolute path
    resolved_abs = resolve_video_path(resolved.resolve())
    assert resolved_abs.is_file(), f"Failed absolute path: {resolved_abs}"

    # 4. By file:// URI
    resolved_uri = resolve_video_path(f"file://{resolved.resolve()}")
    assert resolved_uri.is_file(), f"Failed URI path: {resolved_uri}"
    print("ok path resolution")


def test_routing_and_gateway_bypass() -> None:
    assert protocol_from_source("test.mp4") == "video"
    assert protocol_from_source("file:///path/to/vid.mkv") == "video"
    recovered = unwrap_local_video_source(
        "rtsp://hello@home/george/Documents/Inbound-Surveillance/edge/videos/clip.mp4"
    )
    assert recovered == "/home/george/Documents/Inbound-Surveillance/edge/videos/clip.mp4"
    assert protocol_from_source("test.avi") == "video"
    assert protocol_from_source("test.mov") == "video"
    assert protocol_from_source("test.webm") == "video"

    assert ingest_kind("sample.mp4") == "video"
    assert ingest_kind("something", protocol="video") == "video"
    assert ingest_kind("something", protocol="file") == "video"

    adapter = create_adapter(SAMPLE_VIDEO)
    assert isinstance(adapter, VideoFileAdapter)

    direct = create_direct_adapter("sample_garage_demo.mp4")
    assert isinstance(direct, VideoFileAdapter)

    mangled = "rtsp://hello@home/george/Documents/Inbound-Surveillance/edge/videos/sample_garage_demo.mp4"
    # Even if that exact file is absent, protocol=video must not route to RTSP/FFmpeg.
    from adapters.rtsp import RTSPAdapter
    from adapters.gateway import GatewayAdapter
    class FakeGw:
        def is_ready(self):
            return True
        def consumer_url(self, stream_id, source):
            return f"rtsp://127.0.0.1:8554/{stream_id}"
        client = None
    routed = create_adapter(mangled, protocol="video", gateway=FakeGw(), stream_id="vid")
    assert isinstance(routed, VideoFileAdapter), type(routed)
    assert not isinstance(routed, (RTSPAdapter, GatewayAdapter))
    print("ok routing and gateway bypass")


def test_video_adapter_connect_and_read_10_frames() -> None:
    adapter = VideoFileAdapter(SAMPLE_VIDEO)
    ok = adapter.connect()
    assert ok, f"Connect failed: {adapter.error}"
    assert adapter.is_connected()
    assert adapter.fps == 30.0
    assert abs(adapter.frame_interval - (1.0 / 30.0)) < 1e-4

    t0 = time.time()
    frames = []
    for i in range(10):
        packet = adapter.read_frame()
        assert packet is not None, f"Frame {i} was None"
        assert packet.frame.shape == (720, 1280, 3)
        assert packet.width == 1280
        assert packet.height == 720
        frames.append(packet)

    elapsed = time.time() - t0
    # 10 frames at 30 fps should take ~9 intervals = 9 * 0.0333s = ~0.30s
    # Allow reasonable range [0.20s, 0.60s]
    assert 0.20 <= elapsed <= 0.65, f"Pacing anomaly: elapsed={elapsed:.3f}s for 10 frames"
    adapter.release()
    assert not adapter.is_connected()
    print(f"ok connect and read 10 frames ({elapsed:.3f}s, real-time pace verified)")


def test_video_adapter_seamless_loop() -> None:
    adapter = VideoFileAdapter(SAMPLE_VIDEO)
    ok = adapter.connect()
    assert ok
    total_frames = int(adapter._cap.get(cv2.CAP_PROP_FRAME_COUNT))
    assert total_frames > 10

    # Consume pending first frame
    p1 = adapter.read_frame()
    assert p1 is not None

    # Jump to 2 frames before EOF (frame 448 of 450)
    adapter._cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames - 2)

    # Read frame 448
    f_near_end = adapter.read_frame()
    assert f_near_end is not None

    # Read frame 449 (last frame)
    f_last = adapter.read_frame()
    assert f_last is not None

    # Next read MUST loop back to frame 0 seamlessly
    f_looped = adapter.read_frame()
    assert f_looped is not None, "Loop back returned None"
    assert f_looped.frame.shape == (720, 1280, 3)

    # Subsequent read continues smoothly (frame 1)
    f_next = adapter.read_frame()
    assert f_next is not None

    adapter.release()
    print("ok seamless infinite looping across EOF")


def test_async_frame_grabber_integration() -> None:
    grabber = AsyncFrameGrabber()
    adapter = VideoFileAdapter("sample_garage_demo.mp4")
    grabber.start()
    try:
        grabber.switch_source(adapter)
        deadline = time.time() + 2.0
        frame = None
        while time.time() < deadline:
            frame = grabber.get_latest_frame(timeout=0.1)
            if frame is not None:
                break
        assert frame is not None, "AsyncFrameGrabber failed to produce video frame"
        assert grabber.connection_state == "CONNECTED"
        assert frame.width == 1280
        assert frame.height == 720
    finally:
        grabber.stop()
    print("ok AsyncFrameGrabber integration")


def test_launcher_video_api() -> None:
    from launcher import (
        DATA_DIR,
        ROOT,
        VIDEOS_DIR,
        DashboardRequestHandler,
        init_videos_dir,
    )

    # Verify VIDEOS_DIR exists
    assert VIDEOS_DIR.is_dir()
    sample_symlink = VIDEOS_DIR / "sample_garage_demo.mp4"
    assert sample_symlink.exists()

    # Fake request handler for unit testing do_GET /api/uploaded-videos
    class FakeWfile(io.BytesIO):
        pass

    class DummyServer:
        pass

    class TestHandler(DashboardRequestHandler):
        def __init__(self, method: str, path: str, headers: dict | None = None, body: bytes = b""):
            self.command = method
            self.path = path
            self.headers = headers or {}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.response_status = None
            self.response_headers = {}

        def send_response(self, code: int, message: str | None = None):
            self.response_status = code

        def send_header(self, keyword: str, value: str):
            self.response_headers[keyword] = value

        def end_headers(self):
            pass

    # Test GET /api/uploaded-videos
    get_handler = TestHandler("GET", "/api/uploaded-videos")
    get_handler.do_GET()
    assert get_handler.response_status == 200
    res = json.loads(get_handler.wfile.getvalue().decode("utf-8"))
    assert "videos" in res
    video_names = [v["name"] for v in res["videos"]]
    assert "sample_garage_demo.mp4" in video_names
    print(f"ok GET /api/uploaded-videos found {len(res['videos'])} video(s): {video_names}")

    # Test POST /api/upload-video (binary upload)
    fake_video_content = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    post_handler = TestHandler(
        "POST",
        "/api/upload-video?filename=test_unit_upload.mp4",
        headers={"Content-Length": str(len(fake_video_content))},
        body=fake_video_content,
    )
    post_handler.do_POST()
    assert post_handler.response_status == 200
    upload_res = json.loads(post_handler.wfile.getvalue().decode("utf-8"))
    assert upload_res["success"] is True
    assert upload_res["filename"] == "test_unit_upload.mp4"
    uploaded_path = Path(upload_res["path"])
    assert uploaded_path.exists()
    assert uploaded_path.read_bytes() == fake_video_content
    # Clean up test file
    try:
        uploaded_path.unlink()
    except Exception:
        pass
    print("ok POST /api/upload-video binary upload")

    # Test POST /api/upload-video (multipart upload)
    boundary = "----WebKitFormBoundaryUnit123"
    mp_content = b"fake_multipart_video_stream"
    mp_body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="test_mp_upload.mp4"\r\n'
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode("utf-8") + mp_content + f"\r\n--{boundary}--\r\n".encode("utf-8")

    mp_handler = TestHandler(
        "POST",
        "/api/upload-video",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(mp_body)),
        },
        body=mp_body,
    )
    mp_handler.do_POST()
    assert mp_handler.response_status == 200
    mp_res = json.loads(mp_handler.wfile.getvalue().decode("utf-8"))
    assert mp_res["success"] is True
    assert mp_res["filename"] == "test_mp_upload.mp4"
    mp_uploaded_path = Path(mp_res["path"])
    assert mp_uploaded_path.exists()
    assert mp_uploaded_path.read_bytes() == mp_content
    try:
        mp_uploaded_path.unlink()
    except Exception:
        pass
    print("ok POST /api/upload-video multipart upload")


if __name__ == "__main__":
    test_path_resolution()
    test_routing_and_gateway_bypass()
    test_video_adapter_connect_and_read_10_frames()
    test_video_adapter_seamless_loop()
    test_async_frame_grabber_integration()
    test_launcher_video_api()
    print("\nALL VIDEO FILE STREAMING TESTS PASSED!")
