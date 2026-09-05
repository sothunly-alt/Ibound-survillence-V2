# Implementation Plan: Virtual Camera Streaming Container for ML Demo

## Overview
Build a standalone Docker container (`tools/virtual-camera`) that allows uploading local video files or ingesting online videos (via direct URL or YouTube/yt-dlp) and streams them continuously in real-time over RTSP (and HTTP/WebRTC) in an infinite loop. This simulates physical IP cameras in a garage, allowing Inbound Surveillance's computer vision and ML pipeline (YOLO11 pose detection, vehicle tracking, bay occupancy, and wrench-time calculations) to ingest and analyze the footage identically to real live cameras.

## Architecture Decisions
- **RTSP Streaming Core**: MediaMTX (formerly rtsp-simple-server) paired with FFmpeg in real-time mode (`-re -stream_loop -1`). This provides sub-second latency, zero frame drift, and native compatibility with OpenCV/FFmpeg and go2rtc.
- **Port Strategy**: Default RTSP port mapped to `8556` on the host (since host Inbound Surveillance's embedded `go2rtc` already occupies `8554`), and Web UI on `8090`. All ports configurable via environment variables in `docker-compose.yml`.
- **Ingest Capabilities**:
  1. Direct file upload (MP4, MKV, MOV, AVI, WebM).
  2. Online video link download using `yt-dlp` (supports YouTube, Vimeo, direct MP4 links, CDNs).
  3. Built-in synthetic test video generator for immediate offline testing.
- **Web UI & API**: FastAPI serving a responsive, dark-mode control center with video library, stream status, in-browser live preview, and a 1-click "Copy RTSP URL for Inbound Surveillance" button.
- **Persistence**: Host volume mount `./videos` to persist demo videos across container restarts.

## Task List

### Phase 1: Streaming Core & Process Management
- [ ] Task 1: Create MediaMTX configuration and FFmpeg stream manager (`tools/virtual-camera/stream_manager.py`)
- [ ] Task 2: Build synthetic clip generator for immediate offline testing

### Checkpoint: Streaming Core
- [ ] Stream manager can launch MediaMTX and push an infinite looped real-time RTSP stream.

### Phase 2: API & Video Ingest Engine
- [ ] Task 3: Implement FastAPI application (`tools/virtual-camera/app.py`) with upload and yt-dlp download endpoints
- [ ] Task 4: Add stream control endpoints (start, stop, list channels, stream status)

### Checkpoint: Ingest & API
- [ ] File upload and URL download work, saving videos to `/videos` and exposing stream control via REST.

### Phase 3: Web Dashboard UI
- [ ] Task 5: Build responsive Web UI dashboard (`tools/virtual-camera/static/index.html`, `app.js`, `style.css`)
- [ ] Task 6: Add Inbound Surveillance connection helper card with 1-click copyable RTSP URLs and live video player preview

### Checkpoint: Web UI
- [ ] User can open `http://localhost:8090`, upload a video or paste a URL, start streaming, and copy the RTSP URL.

### Phase 4: Docker Containerization & Verification
- [ ] Task 7: Create `Dockerfile`, `docker-compose.yml`, `.env.example`, and `run.sh`
- [ ] Task 8: Build container and verify end-to-end integration with Inbound Surveillance ML grabber

### Checkpoint: Complete
- [ ] Container runs via `docker compose up -d`.
- [ ] Inbound Surveillance connects to `rtsp://localhost:8556/<channel>` and ML runs successfully.

## Risks and Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| Port conflict with existing `go2rtc` on host port 8554 | High | Default RTSP port set to 8556 in docker-compose. Inbound Surveillance supports custom RTSP ports natively (`rtsp://localhost:8556/channel`). |
| FFmpeg CPU usage high when re-encoding high-res video | Medium | Implement smart codec detection: use `-c:v copy` if source is already H.264, or `-c:v libx264 -preset ultrafast -tune zerolatency` if transcoding is needed. |
| Inbound Surveillance ML grabber timing out on stream start | Medium | MediaMTX holds the RTSP path open; FFmpeg feeds it continuously so clients connecting/disconnecting won't terminate the stream. |
| Online video formats varying widely | Low | `yt-dlp` automatically formats into standard MP4 (H.264 + AAC) compatible with all RTSP clients. |

## Open Questions
- None blocking. Default settings will support both local upload and online video URLs with multi-camera channel support.
