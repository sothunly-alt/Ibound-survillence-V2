from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

from db import day_events, day_minutes


def _parse_open_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def build_report(
    conn,
    day: date,
    venue: str,
    open_time: str,
) -> tuple[str, list[Path]]:
    events = day_events(conn, day)
    minutes = day_minutes(conn, day)
    abandoned = [row for row in events if row["event_type"] == "abandoned"]
    opened_rows = [row for row in events if row["event_type"] == "opened"]

    lines = [
        f"Shift report — {venue}",
        day.isoformat(),
        "",
    ]

    if opened_rows:
        opened_at = datetime.fromisoformat(opened_rows[0]["ts"])
        scheduled = datetime.combine(day, _parse_open_time(open_time))
        delta = opened_at - scheduled
        late_min = int(delta.total_seconds() // 60)
        if late_min > 0:
            lines.append(
                f"Store opened {late_min} min late ({opened_at.strftime('%H:%M')} vs {open_time})."
            )
        else:
            lines.append(f"Store opened {opened_at.strftime('%H:%M')} (on time vs {open_time}).")
    else:
        lines.append("No cashier occupancy recorded today.")

    lines.append(f"Cashier abandoned post {len(abandoned)} times.")

    if minutes:
        peak = max(minutes, key=lambda row: row["max_persons"])
        peak_clock = peak["minute"].split(" ")[-1]
        lines.append(f"Peak traffic at {peak_clock} ({peak['max_persons']} people in frame).")
    else:
        lines.append("Peak traffic: no samples.")

    paths: list[Path] = []
    for row in abandoned:
        if not row["abs_path"]:
            continue
        path = Path(row["abs_path"])
        if path.is_file():
            paths.append(path)
        else:
            print(f"[report] missing proof file: {path}")

    if len(abandoned) > 10:
        lines.append(
            f"Album capped at 10 stills; {len(abandoned) - 10} more remain on disk."
        )

    if len(paths) > 10:
        paths = paths[:9] + [paths[-1]]

    return "\n".join(lines), paths
