"""Ghost Counter on webcam 0 or an NVR RTSP URL.

    cd edge
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python main.py                  # preview; q quit, r shift report
    python main.py --report         # send today's report without a camera

Telegram: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or copy
config.example.yaml to config.yaml and fill the fields.
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import yaml

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import connect, has_opened_today, insert_event, upsert_minute
from occupancy import GhostCounter, box_overlaps_roi, roi_to_pixels
from proof import save_proof
from report import build_report
from telegram_out import TelegramOut


def load_config(path: Path) -> dict:
    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    token = os.environ.get("TELEGRAM_BOT_TOKEN", data.get("telegram_bot_token") or "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", data.get("telegram_chat_id") or "")
    data["telegram_bot_token"] = token
    data["telegram_chat_id"] = chat
    return data


def parse_source(value) -> int | str:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return text


def person_boxes(result) -> tuple[list[tuple[float, float, float, float]], int]:
    boxes: list[tuple[float, float, float, float]] = []
    if result.boxes is None:
        return boxes, 0
    for box in result.boxes:
        if int(box.cls[0]) != 0:
            continue
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
        boxes.append((x1, y1, x2, y2))
    return boxes, len(boxes)


def draw_overlay(
    frame,
    roi_px: tuple[int, int, int, int],
    occupied: bool,
    empty_elapsed: float,
    absent_seconds: float,
) -> None:
    x1, y1, x2, y2 = roi_px
    color = (80, 200, 80) if occupied else (40, 180, 255)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    status = "STAFF IN ROI" if occupied else f"EMPTY {empty_elapsed:.0f}/{absent_seconds:.0f}s"
    cv2.putText(
        frame,
        status,
        (x1, max(24, y1 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        color,
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        "q quit   r send shift report",
        (12, frame.shape[0] - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )


def send_shift_report(conn, cfg: dict, bot: TelegramOut) -> None:
    day = datetime.now().date()
    text, paths = build_report(
        conn,
        day,
        venue=str(cfg.get("venue") or "Store"),
        open_time=str(cfg.get("open_time") or "08:00"),
    )
    print(text)
    bot.send_message(text)
    if paths:
        bot.send_album(paths, caption=f"Proof stills — {day.isoformat()}")
    else:
        print("[report] no proof stills for today")


def run_camera(cfg: dict, conn, bot: TelegramOut) -> None:
    from ultralytics import YOLO

    source = parse_source(cfg.get("source", 0))
    roi = list(cfg.get("roi") or [0.3, 0.2, 0.4, 0.6])
    absent = float(cfg.get("absent_seconds") or 10)
    cooldown = float(cfg.get("cooldown_seconds") or 30)
    sample_fps = float(cfg.get("sample_fps") or 2)
    interval = 1.0 / max(sample_fps, 0.5)
    proofs = ROOT / "proofs"

    cap = cv2.VideoCapture(source)
    if isinstance(source, str) and source.startswith("rtsp"):
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video source: {source}")

    weights = ROOT / "yolov8n.pt"
    model = YOLO(str(weights) if weights.exists() else "yolov8n.pt")
    ghost = GhostCounter(absent, cooldown)
    last_preview = None
    last_infer = 0.0

    print(f"Ingest {source}  ROI {roi}  absent>={absent}s  Telegram={'on' if bot.enabled else 'off'}")
    print("Stand in the green box, then step out to trip the Ghost Counter. Press r for the shift report.")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("[ingest] frame read failed")
            time.sleep(0.25)
            continue

        now = time.time()
        if now - last_infer >= interval:
            last_infer = now
            h, w = frame.shape[:2]
            roi_px = roi_to_pixels(w, h, roi)
            result = model.predict(frame, imgsz=320, verbose=False)[0]
            boxes, person_count = person_boxes(result)
            occupied = any(box_overlaps_roi(box, roi_px) for box in boxes)
            state = ghost.update(occupied, now)
            stamp = datetime.now()

            upsert_minute(
                conn,
                stamp.strftime("%Y-%m-%d %H:%M"),
                person_count,
                state.occupied,
            )
            if state.occupied and not has_opened_today(conn, stamp.date()):
                insert_event(conn, "opened", stamp)

            if state.should_alert:
                path = save_proof(frame, roi_px, stamp, proofs, kind="abandoned")
                insert_event(conn, "abandoned", stamp, str(path))
                caption = (
                    f"{cfg.get('venue', 'Store')}: front desk unattended "
                    f"for {int(absent)}s.\n{stamp.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                print(f"[alert] {path}")
                bot.send_photo(path, caption)

            preview = frame.copy()
            for box in boxes:
                cv2.rectangle(
                    preview,
                    (int(box[0]), int(box[1])),
                    (int(box[2]), int(box[3])),
                    (200, 200, 200),
                    1,
                )
            draw_overlay(preview, roi_px, state.occupied, state.empty_elapsed, absent)
            last_preview = preview

        display = last_preview if last_preview is not None else frame
        cv2.imshow("Inbound — Automated Store Manager", display)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("r"):
            send_shift_report(conn, cfg, bot)

    cap.release()
    cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ghost Counter + shift report on an existing camera (webcam or RTSP)."
    )
    parser.add_argument(
        "--config",
        default="",
        help="YAML config path (default: edge/config.yaml then config.example.yaml)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Send today's shift report and exit (no camera).",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config) if args.config else ROOT / "config.yaml"
    if not cfg_path.exists():
        cfg_path = ROOT / "config.example.yaml"
    cfg = load_config(cfg_path)
    conn = connect(ROOT / "events.db")
    bot = TelegramOut(cfg["telegram_bot_token"], cfg["telegram_chat_id"])

    if args.report:
        send_shift_report(conn, cfg, bot)
        return
    run_camera(cfg, conn, bot)


if __name__ == "__main__":
    main()
