"""Accept a detection as a person only when it has a plausible body skeleton."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2

# COCO-pose indices used by Ultralytics YOLO-pose.
NOSE = 0
L_SHOULDER = 5
R_SHOULDER = 6
TORSO = (NOSE, L_SHOULDER, R_SHOULDER)

SKELETON = (
    (0, 1),
    (0, 2),
    (1, 3),
    (2, 4),
    (5, 6),
    (5, 7),
    (7, 9),
    (6, 8),
    (8, 10),
    (5, 11),
    (6, 12),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
)

Keypoint = tuple[float, float, float]


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    keypoints: list[Keypoint] = field(default_factory=list)
    accepted: bool = False

    def box(self) -> tuple[float, float, float, float]:
        return self.x1, self.y1, self.x2, self.y2


def _as_xyconf(pt) -> Keypoint:
    vals = pt.tolist() if hasattr(pt, "tolist") else list(pt)
    if len(vals) >= 3:
        return float(vals[0]), float(vals[1]), float(vals[2])
    if len(vals) >= 2:
        return float(vals[0]), float(vals[1]), 1.0
    return 0.0, 0.0, 0.0


def extract_keypoints(result, index: int) -> list[Keypoint]:
    kpts = result.keypoints
    if kpts is None:
        return []
    data = getattr(kpts, "data", None)
    if data is None:
        return []
    if index >= len(data):
        return []
    return [_as_xyconf(pt) for pt in data[index]]


def is_human_pose(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    keypoints: list[Keypoint],
    frame_h: int,
    min_height_frac: float = 0.12,
    min_aspect: float = 1.1,
    min_keypoints: int = 4,
    kpt_conf: float = 0.4,
) -> bool:
    height = y2 - y1
    width = max(x2 - x1, 1e-6)
    if height < min_height_frac * max(frame_h, 1):
        return False
    if height / width < min_aspect:
        return False
    visible = [pt for pt in keypoints if pt[2] >= kpt_conf]
    if len(visible) < min_keypoints:
        return False
    torso = 0
    for idx in TORSO:
        if idx < len(keypoints) and keypoints[idx][2] >= kpt_conf:
            torso += 1
    return torso >= 2


def person_detections(
    result,
    frame_h: int,
    conf_min: float = 0.5,
    min_height_frac: float = 0.12,
    min_aspect: float = 1.1,
    min_keypoints: int = 4,
    kpt_conf: float = 0.4,
) -> tuple[list[Detection], list[Detection]]:
    accepted: list[Detection] = []
    rejected: list[Detection] = []
    if result.boxes is None:
        return accepted, rejected
    for i, box in enumerate(result.boxes):
        conf = float(box.conf[0])
        if conf < conf_min:
            continue
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
        keypoints = extract_keypoints(result, i)
        det = Detection(x1, y1, x2, y2, conf, keypoints)
        det.accepted = is_human_pose(
            x1,
            y1,
            x2,
            y2,
            keypoints,
            frame_h,
            min_height_frac=min_height_frac,
            min_aspect=min_aspect,
            min_keypoints=min_keypoints,
            kpt_conf=kpt_conf,
        )
        (accepted if det.accepted else rejected).append(det)
    return accepted, rejected


def draw_skeleton(frame, keypoints: list[Keypoint], kpt_conf: float, color) -> None:
    for a, b in SKELETON:
        if a >= len(keypoints) or b >= len(keypoints):
            continue
        xa, ya, ca = keypoints[a]
        xb, yb, cb = keypoints[b]
        if ca < kpt_conf or cb < kpt_conf:
            continue
        cv2.line(
            frame,
            (int(xa), int(ya)),
            (int(xb), int(yb)),
            color,
            2,
            cv2.LINE_AA,
        )
    for x, y, c in keypoints:
        if c < kpt_conf:
            continue
        cv2.circle(frame, (int(x), int(y)), 3, color, -1, cv2.LINE_AA)


def draw_detection(
    frame,
    det: Detection,
    *,
    in_roi: bool,
    kpt_conf: float = 0.4,
) -> None:
    x1, y1, x2, y2 = (int(det.x1), int(det.y1), int(det.x2), int(det.y2))
    if det.accepted:
        color = (80, 220, 80) if in_roi else (170, 170, 170)
        label = f"person {det.conf:.2f}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        draw_skeleton(frame, det.keypoints, kpt_conf, color)
    else:
        color = (120, 120, 120)
        label = f"blob {det.conf:.2f}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
    cv2.putText(
        frame,
        label,
        (x1, max(16, y1 - 6)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        color,
        1,
        cv2.LINE_AA,
    )


def standing_person_keypoints() -> list[Keypoint]:
    pts = [(0.0, 0.0, 0.0)] * 17
    pts[0] = (100.0, 40.0, 0.9)
    pts[5] = (80.0, 80.0, 0.9)
    pts[6] = (120.0, 80.0, 0.9)
    pts[7] = (70.0, 120.0, 0.8)
    pts[8] = (130.0, 120.0, 0.8)
    pts[11] = (85.0, 150.0, 0.8)
    pts[12] = (115.0, 150.0, 0.8)
    return pts
