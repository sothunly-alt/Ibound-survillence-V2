"""Lightweight ByteTrack-style Kalman tracker with identity persistence.

Keeps a stable track_id across brief occlusions and head turns, and binds a
verified staff name onto the track so occupancy never splits "George" into
"Employee" when the face leaves the camera.

Also where object hallucinations go to die. Pose geometry is single-frame and
an open engine scores 0.75 as easily as a mechanic, so confidence is worthless
as a filter here. What separates them is time: a track whose box, joints, and
pixels are all frozen for seconds on end is furniture, whatever its score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from liveness import (
    DEAD_KEYPOINT_JITTER,
    DEAD_MOTION_ENERGY,
    LivenessProbe,
    keypoint_jitter,
)
from person import Detection, anatomy_is_weak
from reid import BodyReIDExtractor, ReIDGallery

UNKNOWN_LABEL = "Employee"


def _tlbr_to_xywh(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    w = max(x2 - x1, 1.0)
    h = max(y2 - y1, 1.0)
    return (x1 + x2) * 0.5, (y1 + y2) * 0.5, w, h


def _xywh_to_tlbr(cx: float, cy: float, w: float, h: float) -> tuple[float, float, float, float]:
    return cx - w * 0.5, cy - h * 0.5, cx + w * 0.5, cy + h * 0.5


def _bbox_diag(bbox: tuple[float, float, float, float]) -> float:
    x1, y1, x2, y2 = bbox
    return float(max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0))


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
    keypoints: list = field(default_factory=list)
    # None until the first measurement; seeded then, EMA'd after.
    jitter: float | None = None
    liveness: float | None = None
    inanimate_hits: int = 0

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
        # Track ids the caller wants spared from the inanimate sweep — a
        # mechanic wedged under a chassis is legitimately motionless.
        self.protected_ids: set[int] = set()
        # Tracks killed as clutter on the last update, for the negative bank.
        self.clutter_events: list[Track] = []

    def reset(self) -> None:
        self.tracks = []
        self.gallery.clear()
        self._next_id = 1
        self.protected_ids = set()
        self.clutter_events = []

    def is_confirmed(self, track_id: int | None) -> bool:
        if track_id is None:
            return False
        for trk in self.tracks:
            if trk.track_id == track_id:
                return trk.hits >= self.min_hits and not trk.clutter
        return False

    def update(self, detections: list) -> list:
        self.clutter_events = []
        for trk in self.tracks:
            trk.time_since_update += 1
            if trk.kalman is not None:
                trk.bbox = trk.kalman.predict()

        if not detections:
            kept: list[Track] = []
            coasted: list = []
            for trk in self.tracks:
                if trk.time_since_update > self.max_age or trk.clutter:
                    continue
                kept.append(trk)
                if trk.hits >= self.min_hits:
                    coasted.append(self._coasting_detection(trk))
            self.tracks = kept
            return coasted

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

        center_matched = self._match_center(detections, unmatched_dets, unmatched_trks)
        for t_idx, d_idx in center_matched:
            unmatched_dets.discard(d_idx)
            unmatched_trks.discard(t_idx)
            matched.append((t_idx, d_idx))

        for t_idx, d_idx in matched:
            self._update_matched(self.tracks[t_idx], detections[d_idx])

        for d_idx in unmatched_dets:
            self._start_track(detections[d_idx])

        self.clutter_events = [t for t in self.tracks if t.clutter]
        self.tracks = [
            t for t in self.tracks if t.time_since_update <= self.max_age and not t.clutter
        ]
        confirmed_ids = {t.track_id for t in self.tracks if t.hits >= self.min_hits}
        live = [d for d in detections if d.track_id in confirmed_ids]
        live_ids = {d.track_id for d in live}
        for trk in self.tracks:
            if trk.track_id in confirmed_ids and trk.track_id not in live_ids:
                live.append(self._coasting_detection(trk))
        return live

    def _coasting_detection(self, trk: Track) -> Detection:
        x1, y1, x2, y2 = trk.predicted_bbox()
        det = Detection(x1, y1, x2, y2, trk.conf, [])
        det.accepted = True
        det.coasting = True
        det.track_id = trk.track_id
        det.identity = trk.identity
        det.is_staff = trk.is_staff
        det.identity_conf = 0.51 if trk.is_staff else 0.0
        return det

    def _match_center(
        self,
        detections: list,
        unmatched_dets: set[int],
        unmatched_trks: set[int],
    ) -> list[tuple[int, int]]:
        """Keep a shrinking hood-lean box on the same confirmed track when IoU drops."""
        extra: list[tuple[int, int]] = []
        used_d: set[int] = set()
        for t_idx in list(unmatched_trks):
            tx1, ty1, tx2, ty2 = self.tracks[t_idx].predicted_bbox()
            tcx, tcy = (tx1 + tx2) * 0.5, (ty1 + ty2) * 0.5
            tdiag = max(((tx2 - tx1) ** 2 + (ty2 - ty1) ** 2) ** 0.5, 1.0)
            best_d: int | None = None
            best_dist = tdiag * 0.65
            for d_idx in unmatched_dets:
                if d_idx in used_d:
                    continue
                dx1, dy1, dx2, dy2 = detections[d_idx].box()
                dcx, dcy = (dx1 + dx2) * 0.5, (dy1 + dy2) * 0.5
                dist = ((tcx - dcx) ** 2 + (tcy - dcy) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_d = d_idx
            if best_d is not None:
                extra.append((t_idx, best_d))
                used_d.add(best_d)
        return extra

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
        new_keypoints = list(getattr(det, "keypoints", []) or [])
        self._update_liveness(trk, det, new_keypoints)
        trk.keypoints = new_keypoints
        feat = getattr(det, "reid_feat", None)
        if feat is not None:
            trk.reid_features.append(feat)
            if len(trk.reid_features) > 8:
                trk.reid_features = trk.reid_features[-8:]
        if anatomy_is_weak(getattr(det, "keypoints", []) or [], 0.35):
            trk.weak_anatomy_hits += 1
        else:
            trk.weak_anatomy_hits = max(0, trk.weak_anatomy_hits - 1)
        static_long_enough = trk.hits >= self.static_hits and trk.motion < self.static_px
        weak_clutter = static_long_enough and trk.weak_anatomy_hits >= self.static_hits // 2
        if not trk.is_staff and (weak_clutter or self._is_inanimate(trk)):
            trk.clutter = True
        self._bind_identity(trk, det)

    def _update_liveness(self, trk: Track, det, new_keypoints: list) -> None:
        """Fold this frame's joint wobble and pixel churn into the track EMAs."""
        diag = _bbox_diag(trk.bbox)
        jitter = keypoint_jitter(trk.keypoints, new_keypoints, diag)
        if jitter is not None:
            trk.jitter = float(jitter) if trk.jitter is None else 0.7 * trk.jitter + 0.3 * float(jitter)
        energy = getattr(det, "liveness", None)
        if energy is not None:
            trk.liveness = float(energy) if trk.liveness is None else 0.7 * trk.liveness + 0.3 * float(energy)

    def _is_inanimate(self, trk: Track) -> bool:
        """Frozen box AND frozen joints AND frozen pixels, sustained.

        Confidence is deliberately not consulted: the engine-bay and bike-frame
        hallucinations score 0.71-0.76, higher than many real crouched workers.
        Every channel with data must agree, so one noisy signal cannot delete a
        person who happens to be holding still, and a channel that was never
        wired up (no pose data, no frame) simply abstains.
        """
        if trk.track_id in self.protected_ids:
            trk.inanimate_hits = 0
            return False
        frozen = (
            trk.motion < self.static_px
            and trk.jitter is not None
            and trk.jitter < DEAD_KEYPOINT_JITTER
            and (trk.liveness is None or trk.liveness < DEAD_MOTION_ENERGY)
        )
        trk.inanimate_hits = trk.inanimate_hits + 1 if frozen else 0
        return trk.inanimate_hits >= max(6, self.static_hits // 3)

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
            keypoints=list(getattr(det, "keypoints", []) or []),
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
    probe: LivenessProbe | None = None,
) -> list:
    """Measure liveness, extract appearance, run face ID, then lock names on."""
    if probe is not None:
        probe.annotate(frame, detections)
    if reid is not None and frame is not None:
        for det in detections:
            det.reid_feat = reid.extract(frame, det.box())
    if face_rec is not None and frame is not None:
        face_rec.annotate_detections(frame, detections)
    return tracker.update(detections)
