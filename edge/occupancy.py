from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class GhostState:
    occupied: bool
    empty_elapsed: float
    should_alert: bool


class GhostCounter:
    """One alert per empty streak, plus a cooldown so brief flaps do not spam."""

    def __init__(self, absent_seconds: float, cooldown_seconds: float) -> None:
        self.absent_seconds = absent_seconds
        self.cooldown_seconds = cooldown_seconds
        self._empty_since: float | None = None
        self._alerted_this_empty = False
        self._last_alert_at: float | None = None

    def update(self, occupied: bool, now: float) -> GhostState:
        if occupied:
            self._empty_since = None
            self._alerted_this_empty = False
            return GhostState(True, 0.0, False)

        if self._empty_since is None:
            self._empty_since = now
        elapsed = now - self._empty_since
        should = False
        if elapsed >= self.absent_seconds and not self._alerted_this_empty:
            cooled = self._last_alert_at is None or (
                now - self._last_alert_at
            ) >= self.cooldown_seconds
            if cooled:
                should = True
                self._alerted_this_empty = True
                self._last_alert_at = now
        return GhostState(False, elapsed, should)


class OccupancyGate:
    """Fill/drain occupancy so brief false persons cannot reset GhostCounter.

    At any sample rate: ~confirm_seconds of person-in-ROI to go occupied,
    ~clear_seconds of empty to go vacant. A 0.2s flicker never fills the bar.
    """

    def __init__(self, confirm_seconds: float, clear_seconds: float) -> None:
        self.confirm_seconds = max(1e-3, confirm_seconds)
        self.clear_seconds = max(1e-3, clear_seconds)
        self._hold = 0.0
        self._last: float | None = None
        self.occupied = False

    def update(self, detected: bool, now: float) -> bool:
        if self._last is None:
            dt = 0.0
        else:
            dt = max(0.0, min(now - self._last, 1.0))
        self._last = now
        if detected:
            self._hold = min(self.confirm_seconds, self._hold + dt)
            if self._hold >= self.confirm_seconds:
                self.occupied = True
        else:
            drain = dt * (self.confirm_seconds / self.clear_seconds)
            self._hold = max(0.0, self._hold - drain)
            if self._hold <= 0.0:
                self.occupied = False
        return self.occupied


def roi_to_pixels(
    frame_w: int,
    frame_h: int,
    roi: list[float],
) -> tuple[int, int, int, int]:
    x, y, rw, rh = roi
    x1 = max(0, int(x * frame_w))
    y1 = max(0, int(y * frame_h))
    x2 = min(frame_w, int((x + rw) * frame_w))
    y2 = min(frame_h, int((y + rh) * frame_h))
    return x1, y1, x2, y2


def box_overlaps_roi(
    box: tuple[float, float, float, float],
    roi_px: tuple[int, int, int, int],
) -> bool:
    ax1, ay1, ax2, ay2 = box
    bx1, by1, bx2, by2 = roi_px
    return not (ax2 < bx1 or ax1 > bx2 or ay2 < by1 or ay1 > by2)


def box_center_in_roi(
    box: tuple[float, float, float, float],
    roi_px: tuple[int, int, int, int],
) -> bool:
    x1, y1, x2, y2 = box
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    rx1, ry1, rx2, ry2 = roi_px
    return rx1 <= cx <= rx2 and ry1 <= cy <= ry2


def pixels_to_roi(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    frame_w: int,
    frame_h: int,
    min_frac: float = 0.02,
) -> list[float]:
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    x1 = max(0, min(frame_w - 1, x1))
    y1 = max(0, min(frame_h - 1, y1))
    x2 = max(0, min(frame_w, x2))
    y2 = max(0, min(frame_h, y2))
    min_w = max(2, int(min_frac * frame_w))
    min_h = max(2, int(min_frac * frame_h))
    if x2 - x1 < min_w:
        x2 = min(frame_w, x1 + min_w)
        if x2 - x1 < min_w:
            x1 = max(0, x2 - min_w)
    if y2 - y1 < min_h:
        y2 = min(frame_h, y1 + min_h)
        if y2 - y1 < min_h:
            y1 = max(0, y2 - min_h)
    rw = max(min_frac, (x2 - x1) / max(frame_w, 1))
    rh = max(min_frac, (y2 - y1) / max(frame_h, 1))
    x = max(0.0, min(1.0 - rw, x1 / max(frame_w, 1)))
    y = max(0.0, min(1.0 - rh, y1 / max(frame_h, 1)))
    return [x, y, rw, rh]


# --- Auto garage: multi-bay zones + YOLO11 pose work classification ------------

DEFAULT_BAYS: list[dict] = [
    {"id": "bay_1", "name": "Lift Bay 1", "roi": [0.10, 0.20, 0.35, 0.60], "type": "vehicle_bay"},
    {"id": "bay_2", "name": "Bay 2 (Brakes/Tires)", "roi": [0.55, 0.20, 0.35, 0.60], "type": "vehicle_bay"},
    {"id": "tools", "name": "Tool Station", "roi": [0.42, 0.05, 0.16, 0.20], "type": "tool_area"},
]

BAY_TYPES = ("vehicle_bay", "tool_area")
BAY_STATES = ("WORKING", "UNDER_VEHICLE", "ON_BREAK", "IDLE", "EMPTY")
IDLE_STATIONARY_SECONDS = 120.0
UNDER_CAR_GRACE_SECONDS = 5.0
BREAK_TIMEOUT_SECONDS = 3600.0
MAX_ACTIVITY_DT = 2.0

# COCO-pose indices (Ultralytics YOLO-pose).
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

BAY_BGR = {
    "WORKING": (80, 220, 80),
    "UNDER_VEHICLE": (255, 180, 40),
    "ON_BREAK": (180, 140, 255),
    "IDLE": (40, 200, 255),
    "EMPTY": (150, 150, 150),
}
BAY_ID_BGR = {
    "bay_1": (80, 220, 80),
    "bay_2": (40, 180, 255),
    "tools": (220, 90, 220),
}


def clamp_roi(roi: list[float]) -> list[float]:
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        return [0.30, 0.20, 0.40, 0.60]
    try:
        x, y, w, h = (float(v) for v in roi)
    except (TypeError, ValueError):
        return [0.30, 0.20, 0.40, 0.60]
    w = max(0.02, min(1.0, w))
    h = max(0.02, min(1.0, h))
    x = max(0.0, min(1.0 - w, x))
    y = max(0.0, min(1.0 - h, y))
    return [x, y, w, h]


def roi_as_polygon(roi: list[float]) -> list[list[float]]:
    x, y, w, h = clamp_roi(roi)
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def point_in_polygon(px: float, py: float, polygon: list[list[float]]) -> bool:
    """Ray-casting containment. Vertices are (x, y) in any unit (frac or px)."""
    if not polygon or len(polygon) < 3:
        return False
    inside = False
    j = len(polygon) - 1
    for i, vertex in enumerate(polygon):
        xi, yi = float(vertex[0]), float(vertex[1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        intersects = ((yi > py) != (yj > py)) and (
            px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def point_in_roi(px: float, py: float, roi: list[float]) -> bool:
    x, y, w, h = clamp_roi(roi)
    return x <= px <= x + w and y <= py <= y + h


def _as_bay_dict(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    bay_id = str(raw.get("id") or "").strip()
    if not bay_id:
        return None
    name = str(raw.get("name") or bay_id).strip() or bay_id
    bay_type = str(raw.get("type") or "vehicle_bay").strip()
    if bay_type not in BAY_TYPES:
        bay_type = "vehicle_bay"
    roi = clamp_roi(list(raw.get("roi") or [0.30, 0.20, 0.40, 0.60]))
    polygon = raw.get("polygon")
    verts: list[list[float]] | None = None
    if isinstance(polygon, (list, tuple)) and len(polygon) >= 3:
        verts = []
        for pt in polygon:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                try:
                    verts.append([float(pt[0]), float(pt[1])])
                except (TypeError, ValueError):
                    continue
        if len(verts) < 3:
            verts = None
    return {"id": bay_id, "name": name, "roi": roi, "type": bay_type, "polygon": verts}


def normalize_bays(
    raw: object,
    fallback_roi: list[float] | None = None,
    *,
    seed_if_empty: bool = True,
) -> list[dict]:
    """Coerce config bays; seed the three default shop zones when missing.

    An explicit empty list is kept empty when ``seed_if_empty`` is False so
    deleting the last bay does not resurrect the defaults.
    """
    out: list[dict] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            bay = _as_bay_dict(item) if isinstance(item, dict) else None
            if bay is None or bay["id"] in seen:
                continue
            seen.add(bay["id"])
            out.append(bay)
    if out or not seed_if_empty:
        return out
    seeded = [{**b, "roi": list(b["roi"])} for b in DEFAULT_BAYS]
    if fallback_roi:
        seeded[0] = {**seeded[0], "roi": clamp_roi(fallback_roi)}
    return seeded


def next_available_bay_name(bays: object) -> str:
    """Lowest missing 'Bay N' label. Existing names are never rewritten."""
    used: set[int] = set()
    rows = bays if isinstance(bays, list) else []
    for item in rows:
        name = ""
        if isinstance(item, dict):
            name = str(item.get("name") or "")
        else:
            name = str(getattr(item, "name", "") or "")
        match = re.search(r"Bay\s*(\d+)", name, re.I)
        if match:
            used.add(int(match.group(1)))
    num = 1
    while num in used:
        num += 1
    return f"Bay {num}"


def _kpt(keypoints: list, index: int, kpt_conf: float) -> tuple[float, float] | None:
    if index >= len(keypoints):
        return None
    pt = keypoints[index]
    if pt is None or len(pt) < 2:
        return None
    conf = float(pt[2]) if len(pt) >= 3 else 1.0
    if conf < kpt_conf:
        return None
    return float(pt[0]), float(pt[1])


def _mid(
    a: tuple[float, float] | None, b: tuple[float, float] | None
) -> tuple[float, float] | None:
    if a and b:
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    return a or b


def is_working_pose(keypoints: list, kpt_conf: float = 0.4) -> bool:
    """True when YOLO11 pose looks like active wrench work, not idle standing.

    WORKING cues (image y grows downward):
    - wrists / elbows raised above the shoulders (arms into an engine bay)
    - crouched (hips close to knees — wheel-well / caliper work)
    - torso pitched off vertical (leaning under a chassis or hood)
    """
    ls = _kpt(keypoints, L_SHOULDER, kpt_conf)
    rs = _kpt(keypoints, R_SHOULDER, kpt_conf)
    le = _kpt(keypoints, L_ELBOW, kpt_conf)
    re = _kpt(keypoints, R_ELBOW, kpt_conf)
    lw = _kpt(keypoints, L_WRIST, kpt_conf)
    rw = _kpt(keypoints, R_WRIST, kpt_conf)
    lh = _kpt(keypoints, L_HIP, kpt_conf)
    rh = _kpt(keypoints, R_HIP, kpt_conf)
    lk = _kpt(keypoints, L_KNEE, kpt_conf)
    rk = _kpt(keypoints, R_KNEE, kpt_conf)

    shoulders = _mid(ls, rs)
    hips = _mid(lh, rh)
    knees = _mid(lk, rk)
    torso = 0.0
    if shoulders and hips:
        torso = max(1.0, abs(hips[1] - shoulders[1]))

    raise_margin = max(8.0, 0.12 * torso) if torso else 12.0
    for shoulder, wrist, elbow in ((ls, lw, le), (rs, rw, re)):
        if shoulder and wrist and wrist[1] < shoulder[1] - raise_margin:
            return True
        if shoulder and elbow and elbow[1] < shoulder[1] - raise_margin * 0.6:
            return True

    if hips and knees and torso:
        thigh = abs(knees[1] - hips[1])
        if thigh < 0.48 * torso:
            return True

    if shoulders and hips and torso:
        dx = abs(shoulders[0] - hips[0])
        if dx / torso > 0.32:
            return True
    return False


def is_under_vehicle_pose(keypoints: list, kpt_conf: float = 0.35) -> bool:
    """True when pose indicates worker lying on a creeper or working under a vehicle.

    Cues:
    - Lower limbs (ankles/knees) visible while upper torso/head are occluded under chassis.
    - Horizontal body alignment: horizontal distance (dx) between joints significantly exceeds vertical (dy).
    """
    ls = _kpt(keypoints, L_SHOULDER, kpt_conf)
    rs = _kpt(keypoints, R_SHOULDER, kpt_conf)
    lh = _kpt(keypoints, L_HIP, kpt_conf)
    rh = _kpt(keypoints, R_HIP, kpt_conf)
    lk = _kpt(keypoints, L_KNEE, kpt_conf)
    rk = _kpt(keypoints, R_KNEE, kpt_conf)
    la = _kpt(keypoints, L_ANKLE, kpt_conf)
    ra = _kpt(keypoints, R_ANKLE, kpt_conf)

    shoulders = _mid(ls, rs)
    hips = _mid(lh, rh)
    knees = _mid(lk, rk)
    ankles = _mid(la, ra)

    # 1. Lower body limbs visible extending out from chassis (ankles or knees present)
    lower_pts = sum(1 for p in (lh, rh, lk, rk, la, ra) if p is not None)
    upper_pts = sum(1 for p in (ls, rs) if p is not None)
    if lower_pts >= 2 and upper_pts == 0:
        return True

    # 2. Horizontal creeper alignment (shoulders to hips or hips to ankles horizontal: dx > dy)
    if shoulders and hips:
        dx = abs(shoulders[0] - hips[0])
        dy = abs(shoulders[1] - hips[1])
        if dx > 1.2 * dy:
            return True

    if hips and knees:
        dx = abs(hips[0] - knees[0])
        dy = abs(hips[1] - knees[1])
        if dx > 1.2 * dy:
            return True

    if knees and ankles:
        dx = abs(knees[0] - ankles[0])
        dy = abs(knees[1] - ankles[1])
        if dx > 1.2 * dy:
            return True

    return False


def detection_anchor(det) -> tuple[float, float]:
    box = det.box() if hasattr(det, "box") else (det.x1, det.y1, det.x2, det.y2)
    x1, y1, x2, y2 = box
    hips = _mid(
        _kpt(getattr(det, "keypoints", []), L_HIP, 0.25),
        _kpt(getattr(det, "keypoints", []), R_HIP, 0.25),
    )
    if hips:
        return hips
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def detection_in_bay(
    det,
    bay: dict,
    frame_w: int,
    frame_h: int,
    kpt_conf: float = 0.4,
) -> bool:
    ax, ay = detection_anchor(det)
    polygon = bay.get("polygon")
    if polygon:
        px, py = ax / max(frame_w, 1), ay / max(frame_h, 1)
        if point_in_polygon(px, py, polygon):
            return True
    roi_px = roi_to_pixels(frame_w, frame_h, bay["roi"])
    if hasattr(det, "in_roi"):
        return bool(det.in_roi(roi_px, kpt_conf))
    return box_center_in_roi((det.x1, det.y1, det.x2, det.y2), roi_px)


def fmt_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def bay_badge(
    state: str,
    technician: str | None,
    wrench_seconds: float,
    technicians_times: dict[str, float] | None = None,
) -> str:
    if state == "EMPTY":
        return "EMPTY"
    if technicians_times and len(technicians_times) > 1:
        details = ", ".join(f"{name} ({fmt_duration(sec)})" for name, sec in technicians_times.items())
        if state == "UNDER_VEHICLE":
            return f"UNDER VEHICLE - {details}"
        if state == "ON_BREAK":
            return f"ON BREAK - (Paused: {details})"
        if state == "WORKING":
            return f"WORKING - {details}"
        return f"IDLE - {details}"

    name = technician or "Technician"
    if state == "UNDER_VEHICLE":
        return f"UNDER VEHICLE - {name} ({fmt_duration(wrench_seconds)})"
    if state == "ON_BREAK":
        return f"ON BREAK - {name} (Paused: {fmt_duration(wrench_seconds)})"
    if state == "WORKING":
        return f"WORKING - {name} ({fmt_duration(wrench_seconds)})"
    return f"IDLE - {name} ({fmt_duration(wrench_seconds)})"


def bay_draw_color(bay_id: str, state: str) -> tuple[int, int, int]:
    if state in BAY_BGR:
        return BAY_BGR[state]
    return BAY_ID_BGR.get(bay_id, (180, 180, 180))


@dataclass
class BaySnapshot:
    bay_id: str
    name: str
    type: str
    roi: list[float]
    state: str
    mechanic_name: str | None
    wrench_seconds: float
    idle_seconds: float
    under_vehicle_seconds: float
    break_seconds: float
    wrench_time_today: float
    idle_time_today: float
    under_vehicle_today: float
    break_time_today: float
    is_working: bool
    job_id: str | None = None
    vehicle_present: bool = True
    technicians_times: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "bay_id": self.bay_id,
            "name": self.name,
            "type": self.type,
            "roi": list(self.roi),
            "state": self.state,
            "mechanic_name": self.mechanic_name,
            "wrench_seconds": round(self.wrench_seconds, 2),
            "idle_seconds": round(self.idle_seconds, 2),
            "under_vehicle_seconds": round(self.under_vehicle_seconds, 2),
            "break_seconds": round(self.break_seconds, 2),
            "wrench_time_today": round(self.wrench_time_today, 2),
            "idle_time_today": round(self.idle_time_today, 2),
            "under_vehicle_today": round(self.under_vehicle_today, 2),
            "break_time_today": round(self.break_time_today, 2),
            "is_working": self.is_working,
            "job_id": self.job_id,
            "vehicle_present": self.vehicle_present,
            "technicians_times": {k: round(v, 2) for k, v in self.technicians_times.items()},
            "badge": bay_badge(self.state, self.mechanic_name, self.wrench_time_today, self.technicians_times),
        }


class _BayRuntime:
    def __init__(
        self,
        cfg: dict,
        confirm: float,
        clear: float,
        under_car_grace_seconds: float = UNDER_CAR_GRACE_SECONDS,
        break_timeout_seconds: float = BREAK_TIMEOUT_SECONDS,
    ) -> None:
        self.id = cfg["id"]
        self.name = cfg["name"]
        self.type = cfg["type"]
        self.roi = list(cfg["roi"])
        self.polygon = cfg.get("polygon")
        self.gate = OccupancyGate(confirm, clear)
        self.state = "EMPTY"
        self.technician: str | None = None
        self.last_working_technician: str | None = None
        self.technicians_times: dict[str, float] = {}
        self.wrench_seconds = 0.0
        self.idle_seconds = 0.0
        self.under_vehicle_seconds = 0.0
        self.break_seconds = 0.0
        self.today_wrench = 0.0
        self.today_idle = 0.0
        self.today_under_vehicle = 0.0
        self.today_break = 0.0
        self.last_anchor: tuple[float, float] | None = None
        self.stationary_since: float | None = None
        self.last_t: float | None = None
        self.last_active_t: float | None = None
        self.session_open = False
        self.under_car_grace_seconds = float(under_car_grace_seconds)
        self.break_timeout_seconds = float(break_timeout_seconds)
        self.job_id: str | None = cfg.get("job_id")
        self.vehicle_type: str = cfg.get("vehicle_type") or ("vehicle" if cfg["type"] == "vehicle_bay" else "station")
        self.vehicle_present: bool = cfg["type"] == "vehicle_bay"

    def as_config(self) -> dict:
        out = {"id": self.id, "name": self.name, "roi": list(self.roi), "type": self.type}
        if self.polygon:
            out["polygon"] = self.polygon
        if self.job_id:
            out["job_id"] = self.job_id
        return out

    def snapshot(self) -> BaySnapshot:
        return BaySnapshot(
            bay_id=self.id,
            name=self.name,
            type=self.type,
            roi=list(self.roi),
            state=self.state,
            mechanic_name=self.technician,
            wrench_seconds=self.wrench_seconds,
            idle_seconds=self.idle_seconds,
            under_vehicle_seconds=self.under_vehicle_seconds,
            break_seconds=self.break_seconds,
            wrench_time_today=self.today_wrench,
            idle_time_today=self.today_idle,
            under_vehicle_today=self.today_under_vehicle,
            break_time_today=self.today_break,
            is_working=self.state in ("WORKING", "UNDER_VEHICLE"),
            job_id=self.job_id,
            vehicle_present=self.vehicle_present,
            technicians_times=dict(self.technicians_times),
        )


class BayZoneManager:
    """Tracks named service-bay ROIs and pose-based wrench vs idle time.

    Time is accumulated from wall-clock dt between updates so counts do not
    depend on camera or detector frame rate.
    """

    def __init__(
        self,
        bays: object | None = None,
        *,
        idle_stationary_seconds: float = IDLE_STATIONARY_SECONDS,
        occupy_confirm_seconds: float = 1.0,
        occupy_clear_seconds: float = 1.0,
        under_car_grace_seconds: float = UNDER_CAR_GRACE_SECONDS,
        break_timeout_seconds: float = BREAK_TIMEOUT_SECONDS,
        motion_px: float = 14.0,
        fallback_roi: list[float] | None = None,
    ) -> None:
        self.idle_stationary_seconds = max(1.0, float(idle_stationary_seconds))
        self.confirm = occupy_confirm_seconds
        self.clear = occupy_clear_seconds
        self.under_car_grace_seconds = float(under_car_grace_seconds)
        self.break_timeout_seconds = float(break_timeout_seconds)
        self.motion_px = motion_px
        self._bays: list[_BayRuntime] = []
        self.set_bays(bays, fallback_roi=fallback_roi)
        self._last_ticks: list[tuple[str, str | None, bool, float, str, str | None]] = []

    def set_bays(self, bays: object | None, fallback_roi: list[float] | None = None) -> None:
        prev = {b.id: b for b in self._bays}
        rebuilt: list[_BayRuntime] = []
        # An explicit list (even empty) is the operator's config. Seed only
        # when the caller omitted bays entirely so existing metrics stay put.
        seed = not isinstance(bays, list)
        for cfg in normalize_bays(bays, fallback_roi=fallback_roi, seed_if_empty=seed):
            runtime = prev.get(cfg["id"]) or _BayRuntime(
                cfg,
                self.confirm,
                self.clear,
                self.under_car_grace_seconds,
                self.break_timeout_seconds,
            )
            runtime.name = cfg["name"]
            runtime.type = cfg["type"]
            runtime.roi = list(cfg["roi"])
            runtime.polygon = cfg.get("polygon")
            if cfg.get("job_id"):
                runtime.job_id = cfg["job_id"]
            rebuilt.append(runtime)
        self._bays = rebuilt

    def configs(self) -> list[dict]:
        return [b.as_config() for b in self._bays]

    def snapshots(self) -> list[BaySnapshot]:
        return [b.snapshot() for b in self._bays]

    def telemetry(self) -> list[dict]:
        return [s.as_dict() for s in self.snapshots()]

    def hydrate_today(self, bay_rows: list[dict]) -> None:
        by_id = {str(row.get("bay_id") or ""): row for row in bay_rows}
        for bay in self._bays:
            row = by_id.get(bay.id)
            if not row:
                continue
            bay.today_wrench = float(row.get("active_seconds") or row.get("active_duration") or 0)
            bay.today_idle = float(row.get("idle_seconds") or row.get("idle_duration") or 0)
            bay.today_under_vehicle = float(row.get("under_vehicle_seconds") or 0)
            bay.today_break = float(row.get("break_seconds") or 0)

    def update(
        self,
        detections: list,
        frame_w: int,
        frame_h: int,
        now: float,
        kpt_conf: float = 0.4,
    ) -> list[BaySnapshot]:
        ticks: list[tuple[str, str | None, bool, float, str, str | None]] = []
        for bay in self._bays:
            inside = [
                det for det in detections if detection_in_bay(det, bay.as_config(), frame_w, frame_h, kpt_conf)
            ]
            occupied = bay.gate.update(bool(inside), now)
            dt = 0.0
            if bay.last_t is not None:
                dt = max(0.0, min(now - bay.last_t, MAX_ACTIVITY_DT))
            bay.last_t = now

            technician = _pick_technician(inside)
            if technician:
                bay.last_working_technician = technician
            else:
                technician = bay.last_working_technician

            under_vehicle = any(
                is_under_vehicle_pose(getattr(det, "keypoints", []), kpt_conf * 0.85) for det in inside
            )
            working_pose = under_vehicle or any(
                is_working_pose(getattr(det, "keypoints", []), kpt_conf) for det in inside
            )
            moving = _is_moving(bay, inside, self.motion_px)

            if inside:
                bay.session_open = True
                bay.last_active_t = now

                # Track each employee in the bay individually
                active_names: list[str] = []
                for det in inside:
                    name = getattr(det, "identity", None) or ("Staff" if getattr(det, "is_staff", False) else "Employee")
                    if dt > 0:
                        bay.technicians_times[name] = bay.technicians_times.get(name, 0.0) + dt
                    det.active_time_str = fmt_duration(bay.technicians_times.get(name, bay.wrench_seconds + dt))
                    det.bay_name = bay.name
                    if name not in active_names:
                        active_names.append(name)

                technician = ", ".join(active_names) if active_names else (technician or bay.last_working_technician)
                if technician:
                    bay.last_working_technician = technician
                bay.technician = technician

                if under_vehicle:
                    bay.state = "UNDER_VEHICLE"
                    bay.under_vehicle_seconds += dt
                    bay.today_under_vehicle += dt
                else:
                    bay.state = "WORKING"

                if dt > 0:
                    bay.wrench_seconds += dt
                    bay.today_wrench += dt

            else:
                # Person is OUT of the bay -> Stop/pause work timer immediately
                time_since_active = (now - bay.last_active_t) if bay.last_active_t is not None else 999999.0
                if bay.session_open and time_since_active <= bay.break_timeout_seconds:
                    bay.state = "ON_BREAK"
                    bay.technician = technician
                    if dt > 0:
                        bay.break_seconds += dt
                        bay.today_break += dt
                else:
                    bay.state = "EMPTY"
                    bay.technician = None
                    bay.stationary_since = None
                    bay.last_anchor = None
                    bay.session_open = False
                    bay.last_working_technician = None

            is_work_state = bay.state in ("WORKING", "UNDER_VEHICLE")
            ticks.append((bay.id, bay.technician, is_work_state, dt, bay.state, bay.job_id))

        self._last_ticks = ticks
        return self.snapshots()

    def activity_ticks(self) -> list[tuple[str, str | None, bool, float, str, str | None]]:
        return list(getattr(self, "_last_ticks", []))


def _pick_technician(detections: list) -> str | None:
    staff = [
        det
        for det in detections
        if getattr(det, "is_staff", False) and getattr(det, "identity", None)
    ]
    if staff:
        staff.sort(key=lambda d: float(getattr(d, "identity_conf", 0) or 0), reverse=True)
        return str(staff[0].identity)
    named = [det for det in detections if getattr(det, "identity", None)]
    if named:
        return str(named[0].identity)
    return None


def _is_moving(bay: _BayRuntime, detections: list, motion_px: float) -> bool:
    if not detections:
        return False
    ax, ay = detection_anchor(detections[0])
    prev = bay.last_anchor
    bay.last_anchor = (ax, ay)
    if prev is None:
        return False
    dx = ax - prev[0]
    dy = ay - prev[1]
    return (dx * dx + dy * dy) ** 0.5 >= motion_px


def working_pose_keypoints() -> list[tuple[float, float, float]]:
    """Synthetic: arms raised into an engine compartment."""
    pts = [(0.0, 0.0, 0.0)] * 17
    pts[0] = (100.0, 50.0, 0.9)
    pts[5] = (80.0, 90.0, 0.9)
    pts[6] = (120.0, 90.0, 0.9)
    pts[7] = (70.0, 55.0, 0.85)
    pts[8] = (130.0, 55.0, 0.85)
    pts[9] = (65.0, 30.0, 0.85)
    pts[10] = (135.0, 28.0, 0.85)
    pts[11] = (85.0, 160.0, 0.8)
    pts[12] = (115.0, 160.0, 0.8)
    pts[13] = (82.0, 210.0, 0.8)
    pts[14] = (118.0, 210.0, 0.8)
    return pts


def crouching_pose_keypoints() -> list[tuple[float, float, float]]:
    """Synthetic: crouched at a wheel well / caliper."""
    pts = [(0.0, 0.0, 0.0)] * 17
    pts[0] = (110.0, 80.0, 0.9)
    pts[5] = (90.0, 110.0, 0.9)
    pts[6] = (130.0, 115.0, 0.9)
    pts[7] = (75.0, 140.0, 0.8)
    pts[8] = (145.0, 145.0, 0.8)
    pts[9] = (60.0, 165.0, 0.8)
    pts[10] = (155.0, 170.0, 0.8)
    pts[11] = (95.0, 155.0, 0.85)
    pts[12] = (125.0, 158.0, 0.85)
    pts[13] = (90.0, 168.0, 0.85)
    pts[14] = (128.0, 170.0, 0.85)
    return pts


def idle_standing_keypoints() -> list[tuple[float, float, float]]:
    """Synthetic: upright, arms down — idle on the shop floor."""
    pts = [(0.0, 0.0, 0.0)] * 17
    pts[0] = (100.0, 40.0, 0.9)
    pts[5] = (80.0, 80.0, 0.9)
    pts[6] = (120.0, 80.0, 0.9)
    pts[7] = (70.0, 120.0, 0.8)
    pts[8] = (130.0, 120.0, 0.8)
    pts[9] = (68.0, 155.0, 0.8)
    pts[10] = (132.0, 155.0, 0.8)
    pts[11] = (85.0, 150.0, 0.8)
    pts[12] = (115.0, 150.0, 0.8)
    pts[13] = (82.0, 210.0, 0.8)
    pts[14] = (118.0, 210.0, 0.8)
    return pts


def under_vehicle_pose_keypoints() -> list[tuple[float, float, float]]:
    """Synthetic: worker lying horizontally on a creeper / under vehicle."""
    pts = [(0.0, 0.0, 0.0)] * 17
    pts[5] = (60.0, 100.0, 0.9)
    pts[6] = (60.0, 120.0, 0.9)
    pts[7] = (90.0, 100.0, 0.85)
    pts[8] = (90.0, 120.0, 0.85)
    pts[11] = (140.0, 105.0, 0.9)
    pts[12] = (140.0, 115.0, 0.9)
    pts[13] = (190.0, 105.0, 0.85)
    pts[14] = (190.0, 115.0, 0.85)
    pts[15] = (240.0, 105.0, 0.85)
    pts[16] = (240.0, 115.0, 0.85)
    return pts


def partial_legs_pose_keypoints() -> list[tuple[float, float, float]]:
    """Synthetic: only lower legs visible sticking out from under vehicle."""
    pts = [(0.0, 0.0, 0.0)] * 17
    pts[11] = (140.0, 105.0, 0.85)
    pts[12] = (140.0, 115.0, 0.85)
    pts[13] = (190.0, 105.0, 0.85)
    pts[14] = (190.0, 115.0, 0.85)
    pts[15] = (240.0, 105.0, 0.85)
    pts[16] = (240.0, 115.0, 0.85)
    return pts

