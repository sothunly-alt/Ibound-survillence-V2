"""Lightweight ByteTrack-style Kalman tracker with identity persistence.

Keeps a stable track_id across brief occlusions and head turns, and binds a
verified staff name onto the track so occupancy never splits "George" into
"Employee" when the face leaves the camera.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from person import anatomy_is_weak
from reid import BodyReIDExtractor, ReIDGallery

UNKNOWN_LABEL = "Employee"


def _tlbr_to_xywh(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    w = max(x2 - x1, 1.0)
    h = max(y2 - y1, 1.0)
    return (x1 + x2) * 0.5, (y1 + y2) * 0.5, w, h


def _xywh_to_tlbr(cx: float, cy: float, w: float, h: float) -> tuple[float, float, float, float]:
    return cx - w * 0.5, cy - h * 0.5, cx + w * 0.5, cy + h * 0.5


def iou_batch(bboxes1: np.ndarray, bboxes2: np.ndarray) -> np.ndarray:
    if len(bboxes1) == 0 or len(bboxes2) == 0:
        return np.zeros((len(bboxes1), len(bboxes2)), dtype=np.float32)
    x11, y11, x12, y12 = np.split(bboxes1.astype(np.float32), 4, axis=1)
    x21, y21, x22, y22 = np.split(bboxes2.astype(np.float32), 4, axis=1)
    xA = np.maximum(x11, np.transpose(x21))
    yA = np.maximum(y11, np.transpose(y21))
    xB = np.minimum(x12, np.transpose(x22))
    yB = np.minimum(y12, np.transpose(y22))
    inter = np.maximum(0.0, xB - xA) * np.maximum(0.0, yB - yA)
    area1 = np.maximum((x12 - x11) * (y12 - y11), 1e-6)
    area2 = np.maximum((x22 - x21) * (y22 - y21), 1e-6)
    return inter / (area1 + np.transpose(area2) - inter + 1e-6)


def greedy_match(iou_mat: np.ndarray, threshold: float) -> list[tuple[int, int]]:
    """Unique greedy assignment, highest IoU first."""
    if iou_mat.size == 0:
        return []
    pairs: list[tuple[float, int, int]] = []
    rows, cols = iou_mat.shape
    for r in range(rows):
        for c in range(cols):
            score = float(iou_mat[r, c])
            if score >= threshold:
                pairs.append((score, r, c))
    pairs.sort(reverse=True)
    used_r: set[int] = set()
    used_c: set[int] = set()
    matches: list[tuple[int, int]] = []
    for _score, r, c in pairs:
        if r in used_r or c in used_c:
            continue
        used_r.add(r)
        used_c.add(c)
        matches.append((r, c))
    return matches


class _KalmanBox:
    """Constant-velocity filter on (cx, cy, w, h)."""

    def __init__(self, bbox: tuple[float, float, float, float]) -> None:
        cx, cy, w, h = _tlbr_to_xywh(bbox)
        self.cx, self.cy, self.w, self.h = cx, cy, w, h
        self.vx = 0.0
        self.vy = 0.0

    def predict(self) -> tuple[float, float, float, float]:
        self.cx += self.vx
        self.cy += self.vy
        return self.bbox()

    def update(self, bbox: tuple[float, float, float, float]) -> None:
        cx, cy, w, h = _tlbr_to_xywh(bbox)
        self.vx = 0.6 * self.vx + 0.4 * (cx - self.cx)
        self.vy = 0.6 * self.vy + 0.4 * (cy - self.cy)
        self.cx, self.cy, self.w, self.h = cx, cy, w, h

    def bbox(self) -> tuple[float, float, float, float]:
        return _xywh_to_tlbr(self.cx, self.cy, self.w, self.h)


@dataclass
class Track:
    track_id: int
    bbox: tuple[float, float, float, float]
    conf: float
    identity: str | None = None
    is_staff: bool = False
    reid_features: list[np.ndarray] = field(default_factory=list)
    hits: int = 1
    time_since_update: int = 0
    weak_anatomy_hits: int = 0
    motion: float = 0.0
    kalman: _KalmanBox | None = None
    clutter: bool = False

    def predicted_bbox(self) -> tuple[float, float, float, float]:
        if self.kalman is None:
            return self.bbox
        return self.kalman.bbox()

    def prototype(self) -> np.ndarray | None:
        if not self.reid_features:
            return None
        stacked = np.mean(np.stack(self.reid_features, axis=0), axis=0)
        norm = float(np.linalg.norm(stacked))
        return stacked / norm if norm > 1e-6 else stacked


class PersonTracker:
    def __init__(
        self,
        max_age: int = 30,
        min_hits: int = 3,
        iou_threshold: float = 0.3,
        reid_threshold: float = 0.50,
        static_px: float = 3.0,
        static_hits: int = 20,
    ) -> None:
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self.reid_threshold = reid_threshold
        self.static_px = static_px
        self.static_hits = static_hits
        self.tracks: list[Track] = []
        self.gallery = ReIDGallery()
        self._next_id = 1

    def reset(self) -> None:
        self.tracks = []
        self.gallery.clear()
        self._next_id = 1

    def is_confirmed(self, track_id: int | None) -> bool:
        if track_id is None:
            return False
        for trk in self.tracks:
            if trk.track_id == track_id:
                return trk.hits >= self.min_hits and not trk.clutter
        return False

    def update(self, detections: list) -> list:
        for trk in self.tracks:
            trk.time_since_update += 1
            if trk.kalman is not None:
                trk.bbox = trk.kalman.predict()

        if not detections:
            self.tracks = [t for t in self.tracks if t.time_since_update <= self.max_age]
            return []

        det_boxes = np.array([d.box() for d in detections], dtype=np.float32)
        trk_boxes = (
            np.array([t.predicted_bbox() for t in self.tracks], dtype=np.float32)
            if self.tracks
            else np.empty((0, 4), dtype=np.float32)
        )
        matched: list[tuple[int, int]] = []
        if len(trk_boxes) > 0:
            matched = greedy_match(iou_batch(trk_boxes, det_boxes), self.iou_threshold)

        unmatched_dets = set(range(len(detections)))
        unmatched_trks = set(range(len(self.tracks)))
        for t_idx, d_idx in matched:
            unmatched_dets.discard(d_idx)
            unmatched_trks.discard(t_idx)

        reid_matched = self._match_reid(detections, unmatched_dets, unmatched_trks)
        for t_idx, d_idx in reid_matched:
            unmatched_dets.discard(d_idx)
            unmatched_trks.discard(t_idx)
            matched.append((t_idx, d_idx))

        for t_idx, d_idx in matched:
            self._update_matched(self.tracks[t_idx], detections[d_idx])

        for d_idx in unmatched_dets:
            self._start_track(detections[d_idx])

        self.tracks = [
            t for t in self.tracks if t.time_since_update <= self.max_age and not t.clutter
        ]
        confirmed_ids = {t.track_id for t in self.tracks if t.hits >= self.min_hits}
        return [d for d in detections if d.track_id in confirmed_ids]

    def _match_reid(
        self,
        detections: list,
        unmatched_dets: set[int],
        unmatched_trks: set[int],
    ) -> list[tuple[int, int]]:
        extra: list[tuple[int, int]] = []
        used_d: set[int] = set()
        used_t: set[int] = set()
        for d_idx in list(unmatched_dets):
            feat = getattr(detections[d_idx], "reid_feat", None)
            if feat is None:
                continue
            best_t: int | None = None
            best_s = self.reid_threshold
            for t_idx in unmatched_trks:
                if t_idx in used_t:
                    continue
                proto = self.tracks[t_idx].prototype()
                if proto is None:
                    continue
                score = BodyReIDExtractor.cosine_similarity(feat, proto)
                if score > best_s:
                    best_s = score
                    best_t = t_idx
            if best_t is not None:
                extra.append((best_t, d_idx))
                used_d.add(d_idx)
                used_t.add(best_t)
        return extra

    def _named_staff(self, det) -> str | None:
        name = getattr(det, "identity", None)
        if getattr(det, "is_staff", False) and name and name != UNKNOWN_LABEL:
            return str(name)
        return None

    def _bind_identity(self, trk: Track, det) -> None:
        staff_name = self._named_staff(det)
        feat = getattr(det, "reid_feat", None)
        if staff_name:
            trk.identity = staff_name
            trk.is_staff = True
            det.identity = staff_name
            det.is_staff = True
            if feat is not None:
                self.gallery.remember(staff_name, feat)
            return

        if trk.identity and trk.is_staff:
            det.identity = trk.identity
            det.is_staff = True
            det.identity_conf = max(float(getattr(det, "identity_conf", 0) or 0), 0.51)
            return

        gallery_name, score = self.gallery.match(feat, self.reid_threshold)
        if gallery_name:
            trk.identity = gallery_name
            trk.is_staff = True
            det.identity = gallery_name
            det.is_staff = True
            det.identity_conf = score

    def _update_matched(self, trk: Track, det) -> None:
        prev = trk.bbox
        det.track_id = trk.track_id
        trk.bbox = det.box()
        trk.conf = det.conf
        trk.hits += 1
        trk.time_since_update = 0
        if trk.kalman is None:
            trk.kalman = _KalmanBox(trk.bbox)
        else:
            trk.kalman.update(trk.bbox)
        dx = ((prev[0] + prev[2]) * 0.5) - ((trk.bbox[0] + trk.bbox[2]) * 0.5)
        dy = ((prev[1] + prev[3]) * 0.5) - ((trk.bbox[1] + trk.bbox[3]) * 0.5)
        step = float((dx * dx + dy * dy) ** 0.5)
        trk.motion = 0.8 * trk.motion + 0.2 * step
        feat = getattr(det, "reid_feat", None)
        if feat is not None:
            trk.reid_features.append(feat)
            if len(trk.reid_features) > 8:
                trk.reid_features = trk.reid_features[-8:]
        if anatomy_is_weak(getattr(det, "keypoints", []) or [], 0.35):
            trk.weak_anatomy_hits += 1
        else:
            trk.weak_anatomy_hits = max(0, trk.weak_anatomy_hits - 1)
        if (
            trk.hits >= self.static_hits
            and trk.weak_anatomy_hits >= self.static_hits // 2
            and trk.motion < self.static_px
            and not trk.is_staff
        ):
            trk.clutter = True
        self._bind_identity(trk, det)

    def _start_track(self, det) -> None:
        feat = getattr(det, "reid_feat", None)
        gallery_name, score = self.gallery.match(feat, self.reid_threshold)
        staff_name = self._named_staff(det) or gallery_name
        det.track_id = self._next_id
        if gallery_name and not self._named_staff(det):
            det.identity = gallery_name
            det.is_staff = True
            det.identity_conf = score
        trk = Track(
            track_id=self._next_id,
            bbox=det.box(),
            conf=det.conf,
            identity=staff_name,
            is_staff=bool(staff_name),
            kalman=_KalmanBox(det.box()),
        )
        if feat is not None:
            trk.reid_features.append(feat)
            if staff_name:
                self.gallery.remember(staff_name, feat)
        self._next_id += 1
        self.tracks.append(trk)


def run_identity_pipeline(
    frame,
    detections: list,
    tracker: PersonTracker,
    *,
    face_rec=None,
    reid: BodyReIDExtractor | None = None,
) -> list:
    """Extract appearance, run face ID, then lock names onto confirmed tracks."""
    if reid is not None and frame is not None:
        for det in detections:
            det.reid_feat = reid.extract(frame, det.box())
    if face_rec is not None and frame is not None:
        face_rec.annotate_detections(frame, detections)
    return tracker.update(detections)
