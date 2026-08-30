from __future__ import annotations

from dataclasses import dataclass


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
