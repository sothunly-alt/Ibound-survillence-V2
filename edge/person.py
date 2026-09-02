"""Accept a detection as a person only when it has a plausible body skeleton.

YOLO-pose will happily emit 17 noisy keypoints on shoes, bags, and jackets.
Those blobs are rejected here with a kinematic tree check: a living worker must
show a connected torso (or a connected under-vehicle leg chain), not two
floating ankle points.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from occupancy import box_center_in_roi

# COCO-pose indices used by Ultralytics YOLO-pose.
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

TORSO = (NOSE, L_SHOULDER, R_SHOULDER)
TORSO_POINTS = (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)
HEAD_POINTS = (NOSE, L_EYE, R_EYE, L_EAR, R_EAR)
FACE = HEAD_POINTS
FACE_CORE = (NOSE, L_EYE, R_EYE)
LEG_POINTS = (L_KNEE, R_KNEE, L_ANKLE, R_ANKLE)
HIP_POINTS = (L_HIP, R_HIP)

# Biologically connected bones. Disconnected floating points are not a body.
KINEMATIC_EDGES = (
    (NOSE, L_EYE),
    (NOSE, R_EYE),
    (L_EYE, L_EAR),
    (R_EYE, R_EAR),
    (L_SHOULDER, R_SHOULDER),
    (L_SHOULDER, L_ELBOW),
    (L_ELBOW, L_WRIST),
    (R_SHOULDER, R_ELBOW),
    (R_ELBOW, R_WRIST),
    (L_SHOULDER, L_HIP),
    (R_SHOULDER, R_HIP),
    (L_HIP, R_HIP),
    (L_HIP, L_KNEE),
    (L_KNEE, L_ANKLE),
    (R_HIP, R_KNEE),
    (R_KNEE, R_ANKLE),
)
TORSO_EDGES = (
    (L_SHOULDER, R_SHOULDER),
    (L_SHOULDER, L_HIP),
    (R_SHOULDER, R_HIP),
    (L_HIP, R_HIP),
)
LEG_EDGES = (
    (L_HIP, L_KNEE),
    (L_KNEE, L_ANKLE),
    (R_HIP, R_KNEE),
    (R_KNEE, R_ANKLE),
    (L_HIP, R_HIP),
)

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
    track_id: int | None = None
    identity: str | None = None
    identity_conf: float = 0.0
    is_staff: bool = False
    reid_feat: np.ndarray | None = None
    active_time_str: str | None = None
    bay_name: str | None = None

    def box(self) -> tuple[float, float, float, float]:
        return self.x1, self.y1, self.x2, self.y2

    def in_roi(self, roi_px: tuple[int, int, int, int], kpt_conf: float = 0.4) -> bool:
        if box_center_in_roi(self.box(), roi_px):
            return True
        rx1, ry1, rx2, ry2 = roi_px
        hits = 0
        for x, y, c in self.keypoints:
            if c >= kpt_conf and rx1 <= x <= rx2 and ry1 <= y <= ry2:
                hits += 1
        return hits >= 2


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


def _count_visible(keypoints: list[Keypoint], indices: tuple[int, ...], kpt_conf: float) -> int:
    n = 0
    for idx in indices:
        if idx < len(keypoints) and keypoints[idx][2] >= kpt_conf:
            n += 1
    return n


def _pt(keypoints: list[Keypoint], index: int, kpt_conf: float) -> tuple[float, float] | None:
    if index >= len(keypoints):
        return None
    x, y, c = keypoints[index]
    if c < kpt_conf:
        return None
    return float(x), float(y)


def _bbox_diag(x1: float, y1: float, x2: float, y2: float) -> float:
    return float(max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0))


def _bone_ok(
    keypoints: list[Keypoint],
    a: int,
    b: int,
    diag: float,
    kpt_conf: float,
    min_frac: float = 0.045,
    max_frac: float = 0.90,
) -> bool:
    """True when both joints are visible and the segment length is anatomical."""
    pa = _pt(keypoints, a, kpt_conf)
    pb = _pt(keypoints, b, kpt_conf)
    if pa is None or pb is None:
        return False
    dist = ((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5
    return min_frac * diag <= dist <= max_frac * diag


def count_valid_bones(
    keypoints: list[Keypoint],
    edges: tuple[tuple[int, int], ...],
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    kpt_conf: float,
) -> int:
    diag = _bbox_diag(x1, y1, x2, y2)
    return sum(1 for a, b in edges if _bone_ok(keypoints, a, b, diag, kpt_conf))


def is_face_closeup(keypoints: list[Keypoint], kpt_conf: float) -> bool:
    """Laptop webcam: head fills the frame, shoulders are often cropped out."""
    return _count_visible(keypoints, FACE, kpt_conf) >= 3 and _count_visible(
        keypoints, FACE_CORE, kpt_conf
    ) >= 2


def anatomy_is_weak(keypoints: list[Keypoint], kpt_conf: float) -> bool:
    """True when the skeleton looks like clutter (no head, no torso girdle)."""
    head_visible = _count_visible(keypoints, HEAD_POINTS, kpt_conf)
    torso_visible = _count_visible(keypoints, TORSO_POINTS, kpt_conf)
    return head_visible == 0 and torso_visible < 2


def is_creeper_or_underbody_pose(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    keypoints: list[Keypoint],
    frame_h: int,
    min_dim_frac: float = 0.08,
    kpt_conf: float = 0.35,
) -> bool:
    """Worker lying horizontally under a chassis or on a creeper.

    Requires a connected torso or a connected hip–knee–ankle chain.
    A pair of shoes (two hallucinated ankles, no hips) is not a worker.
    """
    width = max(x2 - x1, 1e-6)
    height = max(y2 - y1, 1e-6)
    max_dim = max(width, height)
    if max_dim < min_dim_frac * max(frame_h, 1):
        return False

    torso_visible = _count_visible(keypoints, TORSO_POINTS, kpt_conf)
    legs_visible = _count_visible(keypoints, LEG_POINTS, kpt_conf)
    hips_visible = _count_visible(keypoints, HIP_POINTS, kpt_conf)
    torso_bones = count_valid_bones(keypoints, TORSO_EDGES, x1, y1, x2, y2, kpt_conf)
    leg_bones = count_valid_bones(keypoints, LEG_EDGES, x1, y1, x2, y2, kpt_conf)

    # Standing/crouching torso plus at least one connected limb.
    if torso_visible >= 2 and torso_bones >= 1 and (legs_visible >= 1 or torso_visible >= 3):
        return True
    if torso_visible >= 3 and torso_bones >= 1:
        return True

    # Occluded under-vehicle: hips must be present and joined to knees/ankles.
    # Two disconnected shoe points never form a hip–knee bone.
    if hips_visible >= 1 and legs_visible >= 2 and leg_bones >= 2:
        return True
    return False


def is_human_pose(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    keypoints: list[Keypoint],
    frame_h: int,
    min_height_frac: float = 0.10,
    min_aspect: float = 0.85,
    min_keypoints: int = 4,
    kpt_conf: float = 0.35,
) -> bool:
    """Rigorous human pose kinematic validation.

    Rejects inanimate objects (shoes, backpacks, jackets, chairs) whose
    keypoints are disconnected or lack an upper-body / hip girdle.
    """
    height = y2 - y1
    width = max(x2 - x1, 1e-6)

    if is_creeper_or_underbody_pose(
        x1, y1, x2, y2, keypoints, frame_h, min_height_frac * 0.7, kpt_conf
    ):
        return True

    if height < min_height_frac * max(frame_h, 1):
        return False

    if is_face_closeup(keypoints, kpt_conf):
        return height / width >= 0.50

    visible_pts = [pt for pt in keypoints if pt[2] >= kpt_conf]
    if len(visible_pts) < min_keypoints:
        return False

    head_visible = _count_visible(keypoints, HEAD_POINTS, kpt_conf)
    torso_visible = _count_visible(keypoints, TORSO_POINTS, kpt_conf)
    legs_visible = _count_visible(keypoints, LEG_POINTS, kpt_conf)
    bones = count_valid_bones(keypoints, KINEMATIC_EDGES, x1, y1, x2, y2, kpt_conf)

    # Anti-object: desk clutter has no head and no connected torso girdle.
    if head_visible == 0 and torso_visible < 2:
        return False
    if bones < 2:
        return False

    if (head_visible >= 1 or torso_visible >= 2) and (torso_visible >= 1 or legs_visible >= 1):
        if min_aspect > 0 and height / width < min_aspect * 0.55 and torso_visible < 2:
            return False
        return True

    return torso_visible >= 3 and bones >= 2


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
    time_badge = f" ({det.active_time_str})" if getattr(det, "active_time_str", None) else ""
    if det.accepted:
        color = (80, 220, 80) if in_roi else (170, 170, 170)
        if det.identity and det.is_staff:
            label = f"[Staff: {det.identity}]{time_badge} {det.conf:.2f}"
            color = (50, 240, 50) if in_roi else (100, 200, 100)
        elif det.identity:
            label = f"[{det.identity}]{time_badge} {det.conf:.2f}"
            color = (0, 165, 255) if in_roi else (170, 170, 170)
        else:
            label = f"person{time_badge} {det.conf:.2f}"
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


def closeup_face_keypoints() -> list[Keypoint]:
    pts = [(0.0, 0.0, 0.0)] * 17
    pts[0] = (100.0, 80.0, 0.9)
    pts[1] = (90.0, 70.0, 0.85)
    pts[2] = (110.0, 70.0, 0.85)
    pts[3] = (80.0, 80.0, 0.7)
    pts[4] = (120.0, 80.0, 0.7)
    return pts


def shoe_pair_keypoints() -> list[Keypoint]:
    """Hallucinated ankles/knees on a pair of boots — no hips, no torso."""
    pts = [(0.0, 0.0, 0.0)] * 17
    pts[13] = (90.0, 180.0, 0.42)
    pts[14] = (130.0, 182.0, 0.40)
    pts[15] = (88.0, 210.0, 0.48)
    pts[16] = (132.0, 212.0, 0.45)
    return pts


def backpack_clutter_keypoints() -> list[Keypoint]:
    """Disconnected floating points typical of a bag or folded jacket."""
    pts = [(0.0, 0.0, 0.0)] * 17
    pts[9] = (70.0, 90.0, 0.38)
    pts[13] = (95.0, 170.0, 0.36)
    pts[15] = (140.0, 200.0, 0.41)
    pts[16] = (60.0, 205.0, 0.37)
    return pts
