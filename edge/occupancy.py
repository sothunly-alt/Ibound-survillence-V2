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
