# Virtual Camera Streaming Container - Tasks

## Task 1: Create MediaMTX configuration and FFmpeg stream manager
**Description:** Set up the directory structure in `tools/virtual-camera/`, create `mediamtx.yml` configured for RTSP/WebRTC/HLS, and implement `stream_manager.py` in Python to spawn MediaMTX and manage continuous looping FFmpeg processes (`-stream_loop -1 -re`) per stream channel.
**Acceptance criteria:**
- [x] `mediamtx.yml` configures RTSP on port 8554 (inside container), WebRTC on 8889, and HLS on 8888.
- [x] `stream_manager.py` can start/stop streaming any video file to a named RTSP channel.
- [x] Streams loop infinitely and stream at 1.0x real-time speed.
**Verification:**
- [x] Stream manager tested and active in container; supervises MediaMTX and looping FFmpeg streams.
**Dependencies:** None
**Files touched:**
- `tools/virtual-camera/mediamtx.yml`
- `tools/virtual-camera/stream_manager.py`
**Estimated scope:** Small (2 files)

---

## Task 2: Build synthetic clip generator for offline testing
**Description:** Implement `generate_test_clip.py` that creates an artificial garage-like test video with moving rectangles/timestamp overlay using OpenCV or FFmpeg, so that tests and demos can run immediately without requiring an external video download.
**Acceptance criteria:**
- [x] Generates a 15-second 1080p/720p H.264 MP4 with timestamp and motion.
- [x] Can be triggered via CLI or automatically on startup if the video folder is empty.
**Verification:**
- [x] File is written and readable by OpenCV / FFprobe.
**Dependencies:** Task 1
**Files touched:**
- `tools/virtual-camera/generate_test_clip.py`
**Estimated scope:** XS (1 file)

---

## Checkpoint 1: Streaming Core
- [x] MediaMTX starts and FFmpeg streams synthetic clip to `rtsp://localhost:8554/garage`
- [x] OpenCV `VideoCapture` can read frames from the RTSP stream

---

## Task 3: Implement FastAPI application with Upload and yt-dlp Download
**Description:** Implement `app.py` providing REST endpoints: `POST /api/upload` (multipart file upload with chunking), `POST /api/download-url` (background download using `yt-dlp`), and `GET /api/videos` (list available videos with file size and duration).
**Acceptance criteria:**
- [x] Accepts MP4, MKV, MOV, AVI, WebM files up to 2GB.
- [x] Ingests online URLs (YouTube, direct MP4, CDN links) via `yt-dlp` and saves as MP4.
- [x] Returns progress status for background downloads.
**Verification:**
- [x] Verified curl upload of a test clip (`POST /api/upload` returned 200 OK).
- [x] Verified `POST /api/generate-sample` and download task tracking.
**Dependencies:** Task 1
**Files touched:**
- `tools/virtual-camera/app.py`
- `tools/virtual-camera/requirements.txt`
**Estimated scope:** Medium (2 files)

---

## Task 4: Implement Stream Control REST Endpoints
**Description:** Add endpoints in `app.py` for `/api/streams` (list active camera channels), `/api/streams/start` (start streaming a video to a channel name, e.g. `garage`), `/api/streams/stop` (stop streaming a channel), and `/api/streams/restart`.
**Acceptance criteria:**
- [x] Can assign any uploaded video to a named channel (e.g. `garage`, `bay1`, `bay2`).
- [x] Exposes exact RTSP and WebRTC stream URLs for each channel.
- [x] Gracefully handles switching a channel from one video to another.
**Verification:**
- [x] POST `/api/streams/start` started `bay1` stream with PID 75 and reported streaming status.
- [x] GET `/api/streams` verified concurrent multi-channel streaming (`garage` and `bay1`).
**Dependencies:** Task 1, Task 3
**Files touched:**
- `tools/virtual-camera/app.py`
**Estimated scope:** Small (1 file)

---

## Checkpoint 2: Ingest & API
- [x] Upload, download, and stream control APIs fully functional via HTTP requests.

---

## Task 5: Build Web Dashboard UI
**Description:** Create a modern, dark-themed responsive single-page web UI in `tools/virtual-camera/static/` with drag-and-drop file upload, URL input with download progress, video library manager, and active stream controls.
**Acceptance criteria:**
- [x] Clean drag-and-drop upload zone with upload percentage progress bar.
- [x] URL download input with "Download & Stream" button.
- [x] Video library showing thumbnail/icon, filename, file size, and "Stream" action button.
- [x] Active streams panel showing live status pill (Streaming / Stopped), channel name, and currently playing video.
**Verification:**
- [x] Verified UI assets served cleanly at `http://localhost:8090/`.
**Dependencies:** Task 3, Task 4
**Files touched:**
- `tools/virtual-camera/static/index.html`
- `tools/virtual-camera/static/app.js`
- `tools/virtual-camera/static/style.css`
**Estimated scope:** Medium (3 files)

---

## Task 6: Add Inbound Surveillance Quick-Connect Helper & Video Preview
**Description:** Add an interactive Inbound Surveillance Integration card to the dashboard that provides 1-click copyable RTSP URLs (`rtsp://localhost:8556/<channel>`), pre-filled parameters for `edge/hub.html`, and a live browser video preview element.
**Acceptance criteria:**
- [x] "Copy for Inbound Surveillance" button copies the exact RTSP URL to clipboard.
- [x] Displays live stream preview in browser via WebRTC or HLS.
- [x] Clear step-by-step instructions showing where to paste the URL in the Inbound Surveillance Hub.
**Verification:**
- [x] Tested copy button and verified instructions display exact parameters for `edge/hub.html`.
**Dependencies:** Task 5
**Files touched:**
- `tools/virtual-camera/static/index.html`
- `tools/virtual-camera/static/app.js`
**Estimated scope:** Small (2 files)

---

## Checkpoint 3: UI & Helper
- [x] Complete user journey verified in browser: upload video -> click stream -> copy RTSP URL -> preview stream.

---

## Task 7: Docker Containerization & Docker Compose Setup
**Description:** Create a clean `Dockerfile` (installing MediaMTX, FFmpeg, Python 3, yt-dlp), `docker-compose.yml` with host volume mapping for `./videos` and host port mappings (`8090:8090` for Web UI, `8556:8554` for RTSP, `8889:8889` for WebRTC), `.env.example`, and quick-start scripts `run.sh` / `stop.sh`.
**Acceptance criteria:**
- [x] `Dockerfile` builds without errors.
- [x] `docker compose up -d` starts the service containerized.
- [x] `./videos` directory on the host persists uploaded videos.
- [x] Default host ports: 8090 (Web UI), 8556 (RTSP) — no conflict with host's `go2rtc` on 8554.
**Verification:**
- [x] Docker image `virtual-camera-virtual-camera:latest` built and running.
- [x] Verified `docker compose ps` shows healthy container running with port mappings.
**Dependencies:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6
**Files touched:**
- `tools/virtual-camera/Dockerfile`
- `tools/virtual-camera/docker-compose.yml`
- `tools/virtual-camera/.env.example`
- `tools/virtual-camera/run.sh`
- `tools/virtual-camera/stop.sh`
- `tools/virtual-camera/README.md`
**Estimated scope:** Medium (6 files)

---

## Task 8: End-to-End Integration Verification with Inbound Surveillance
**Description:** Start the virtual camera container, stream a test video on channel `garage`, and connect Inbound Surveillance's backend (`edge/adapters/rtsp.py` and `edge/launcher.py`) to `rtsp://127.0.0.1:8556/garage`. Verify that frames are ingested by `AsyncFrameGrabber` and YOLO ML inference executes without error.
**Acceptance criteria:**
- [x] RTSP socket probe succeeds on port 8556.
- [x] `AsyncFrameGrabber` / `RTSPAdapter` receives consecutive valid `FramePacket` instances.
- [x] Inbound Surveillance YOLO model runs inference on stream frames without error.
**Verification:**
- [x] Ran `verify_stream.py`: received 15 frames at 1280x720 in real time.
- [x] Ran `RTSPAdapter` test in `edge`: packet received `1280x720`.
- [x] Ran YOLOv8 model on captured frames from stream: inference executed cleanly.
**Dependencies:** Task 7
**Files touched:**
- `tools/virtual-camera/verify_stream.py`
**Estimated scope:** Small (1-2 files)

---

## Checkpoint 4: Complete System
- [x] Docker container is running (`inbound-virtual-camera`).
- [x] User can upload any video via Web UI at `http://localhost:8090` or download an online video URL.
- [x] Video streams infinitely as an RTSP camera at `rtsp://localhost:8556/<channel>`.
- [x] Inbound Surveillance ML connects and analyzes the stream like a live physical camera.
