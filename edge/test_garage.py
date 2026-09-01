"""Garage operations: multi-bay pose, attendance, Wi-Fi, scorecards, APIs."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import (
    close_bay_sessions,
    connect,
    get_daily_garage_summary,
    record_face_clock_in,
    record_face_clock_out,
    update_technician_activity,
)
from occupancy import (
    DEFAULT_BAYS,
    BayZoneManager,
    crouching_pose_keypoints,
    idle_standing_keypoints,
    is_working_pose,
    next_available_bay_name,
    normalize_bays,
    point_in_polygon,
    point_in_roi,
    roi_as_polygon,
    working_pose_keypoints,
)
from report import build_garage_report, efficiency_badge
from sensors.wifi_tracker import WifiTracker, parse_arp_table, presence_status


class _Det:
    def __init__(self, kpts, *, x1=180, y1=220, x2=320, y2=620, name="Hour-Meng", staff=True):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.conf = 0.9
        self.keypoints = kpts
        self.accepted = True
        self.identity = name
        self.identity_conf = 0.9
        self.is_staff = staff

    def box(self):
        return self.x1, self.y1, self.x2, self.y2

    def in_roi(self, roi_px, kpt_conf=0.4):
        from occupancy import box_center_in_roi

        return box_center_in_roi(self.box(), roi_px)


def _shift_kpts(kpts, dx: float, dy: float):
    return [(x + dx, y + dy, c) for x, y, c in kpts]


def _det(kpts, **kwargs) -> _Det:
    return _Det(kpts, **kwargs)


class PoseAndRoiTests(unittest.TestCase):
    def test_point_in_polygon_and_roi(self):
        roi = [0.10, 0.20, 0.35, 0.60]
        poly = roi_as_polygon(roi)
        self.assertTrue(point_in_roi(0.20, 0.40, roi))
        self.assertTrue(point_in_polygon(0.20, 0.40, poly))
        self.assertFalse(point_in_roi(0.90, 0.10, roi))
        self.assertFalse(point_in_polygon(0.90, 0.10, poly))
        self.assertTrue(point_in_polygon(0.60, 0.40, roi_as_polygon(DEFAULT_BAYS[1]["roi"])))
        self.assertTrue(point_in_roi(0.50, 0.12, DEFAULT_BAYS[2]["roi"]))

    def test_next_available_bay_name_fills_gap(self):
        remaining = [
            {"id": "bay_1", "name": "Bay 1"},
            {"id": "bay_2", "name": "Bay 2"},
            {"id": "bay_4", "name": "Bay 4"},
            {"id": "bay_5", "name": "Bay 5"},
        ]
        self.assertEqual(next_available_bay_name(remaining), "Bay 3")
        deleted = [b for b in remaining if b["id"] != "bay_4"]
        names = [b["name"] for b in deleted]
        self.assertEqual(names, ["Bay 1", "Bay 2", "Bay 5"])
        self.assertEqual(next_available_bay_name(deleted), "Bay 3")

    def test_delete_bay_keeps_sibling_metrics(self):
        manager = BayZoneManager(
            [
                {"id": "bay_1", "name": "Bay 1", "roi": [0.10, 0.20, 0.35, 0.60], "type": "vehicle_bay"},
                {"id": "bay_2", "name": "Bay 2", "roi": [0.55, 0.20, 0.35, 0.60], "type": "vehicle_bay"},
                {"id": "bay_3", "name": "Bay 3", "roi": [0.10, 0.70, 0.20, 0.20], "type": "vehicle_bay"},
            ],
            occupy_confirm_seconds=0.01,
            occupy_clear_seconds=0.01,
        )
        work = _det(_shift_kpts(working_pose_keypoints(), 80, 280))
        t0 = 50.0
        manager.update([work], 1000, 1000, t0, kpt_conf=0.4)
        manager.update([work], 1000, 1000, t0 + 2.0, kpt_conf=0.4)
        wrench_before = {s.bay_id: s.wrench_seconds for s in manager.snapshots()}
        manager.set_bays(
            [
                {"id": "bay_1", "name": "Bay 1", "roi": [0.10, 0.20, 0.35, 0.60], "type": "vehicle_bay"},
                {"id": "bay_2", "name": "Bay 2", "roi": [0.55, 0.20, 0.35, 0.60], "type": "vehicle_bay"},
            ]
        )
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertNotIn("bay_3", snaps)
        self.assertEqual(snaps["bay_1"].name, "Bay 1")
        self.assertEqual(snaps["bay_2"].name, "Bay 2")
        self.assertGreater(snaps["bay_1"].wrench_seconds, 0)
        self.assertEqual(snaps["bay_1"].wrench_seconds, wrench_before["bay_1"])

    def test_close_bay_sessions_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "events.db")
            ended = datetime(2026, 9, 1, 8, 0, 0)
            opened = datetime(2026, 9, 1, 10, 0, 0)
            now = datetime(2026, 9, 1, 11, 0, 0)
            conn.execute(
                """
                INSERT INTO bay_sessions (bay_id, technician_name, start_time, end_time, active_duration)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("bay_3", "Hour-Meng", ended.isoformat(timespec="seconds"), ended.isoformat(timespec="seconds"), 12.0),
            )
            conn.execute(
                """
                INSERT INTO bay_sessions (bay_id, technician_name, start_time, active_duration)
                VALUES (?, ?, ?, ?)
                """,
                ("bay_3", "Hour-Meng", opened.isoformat(timespec="seconds"), 4.0),
            )
            conn.commit()
            closed = close_bay_sessions(conn, ["bay_3"], now)
            self.assertEqual(closed, 1)
            rows = list(conn.execute("SELECT * FROM bay_sessions WHERE bay_id = ? ORDER BY id", ("bay_3",)))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["end_time"], ended.isoformat(timespec="seconds"))
            self.assertEqual(rows[0]["active_duration"], 12.0)
            self.assertIsNotNone(rows[1]["end_time"])
            self.assertEqual(rows[1]["active_duration"], 4.0)
            conn.close()

    def test_normalize_bays_keeps_explicit_empty(self):
        self.assertEqual(normalize_bays([], seed_if_empty=False), [])
        seeded = normalize_bays([])
        self.assertGreaterEqual(len(seeded), 3)

    def test_pose_classification(self):
        self.assertTrue(is_working_pose(working_pose_keypoints()))
        self.assertTrue(is_working_pose(crouching_pose_keypoints()))
        self.assertFalse(is_working_pose(idle_standing_keypoints()))

    def test_multi_bay_states_and_wrench_dt(self):
        manager = BayZoneManager(
            DEFAULT_BAYS,
            idle_stationary_seconds=0.4,
            occupy_confirm_seconds=0.01,
            occupy_clear_seconds=0.01,
        )
        work = _det(_shift_kpts(working_pose_keypoints(), 80, 280))
        idle = _det(_shift_kpts(idle_standing_keypoints(), 500, 280), name="Sothun", x1=620, y1=220, x2=760, y2=620)
        t0 = 100.0
        manager.update([work, idle], 1000, 1000, t0, kpt_conf=0.4)
        manager.update([work, idle], 1000, 1000, t0 + 1.0, kpt_conf=0.4)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertEqual(snaps["bay_1"].state, "WORKING")
        self.assertEqual(snaps["bay_1"].mechanic_name, "Hour-Meng")
        self.assertGreater(snaps["bay_1"].wrench_seconds, 0.9)
        self.assertEqual(snaps["tools"].state, "EMPTY")
        manager.update([work, idle], 1000, 1000, t0 + 1.6, kpt_conf=0.4)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertIn(snaps["bay_2"].state, ("WORKING", "IDLE"))
        self.assertGreater(snaps["bay_2"].idle_seconds + snaps["bay_2"].wrench_seconds, 0.4)
        empty = manager.update([], 1000, 1000, t0 + 3.0, kpt_conf=0.4)
        by_id = {s.bay_id: s.state for s in empty}
        self.assertEqual(by_id["bay_1"], "EMPTY")
        self.assertEqual(by_id["bay_2"], "EMPTY")
        self.assertEqual(by_id["tools"], "EMPTY")


class AttendanceAndScorecardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "events.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_clock_in_out_and_shift_hours(self):
        start = datetime(2026, 9, 1, 8, 5, 0)
        record_face_clock_in(self.conn, "Hour-Meng", start)
        again = record_face_clock_in(self.conn, "Hour-Meng", start + timedelta(minutes=10))
        self.assertFalse(again["created"])
        record_face_clock_out(self.conn, "Hour-Meng", start + timedelta(hours=8, minutes=5))
        summary = get_daily_garage_summary(self.conn, start.date())
        tech = summary["technicians"][0]
        self.assertEqual(tech["staff_name"], "Hour-Meng")
        self.assertAlmostEqual(tech["total_shift_minutes"], 485.0, places=0)
        self.assertFalse(tech["clocked_in"])

    def test_wrench_percent_and_telegram_scorecard(self):
        day = datetime(2026, 9, 1, 8, 0, 0)
        record_face_clock_in(self.conn, "Hour-Meng", day)
        update_technician_activity(self.conn, "Hour-Meng", "bay_1", True, 180 * 60, day + timedelta(minutes=1))
        update_technician_activity(self.conn, "Hour-Meng", "bay_1", False, 60 * 60, day + timedelta(hours=4))
        record_face_clock_out(self.conn, "Hour-Meng", day + timedelta(hours=6))
        summary = get_daily_garage_summary(
            self.conn, day.date(), open_time="08:00", close_time="18:00", bay_ids=["bay_1", "bay_2", "tools"]
        )
        tech = summary["technicians"][0]
        self.assertAlmostEqual(tech["performance_score"], 50.0, places=0)
        self.assertEqual(efficiency_badge(tech["performance_score"]), "🟡 Normal")
        text, _paths = build_garage_report(
            self.conn, day.date(), "Demo Garage", "08:00", close_time="18:00",
            bay_ids=["bay_1", "bay_2", "tools"],
        )
        self.assertIn("Demo Garage", text)
        self.assertIn("Hour-Meng", text)
        self.assertIn("Wrench", text)
        self.assertIn("bay_1", text)
        self.assertGreaterEqual(len(summary["bays"]), 3)


class WifiPresenceTests(unittest.TestCase):
    def test_arp_parse_and_two_factor(self):
        table = (
            "IP address       HW type     Flags       HW address            Mask     Device\n"
            "192.168.1.42     0x1         0x2         aa:bb:cc:dd:ee:ff     *        wlan0\n"
            "host (192.168.1.50) at 11:22:33:44:55:66 [ether] on wlan0\n"
        )
        rows = parse_arp_table(table)
        macs = {row["mac"] for row in rows}
        self.assertIn("aa:bb:cc:dd:ee:ff", macs)
        self.assertIn("11:22:33:44:55:66", macs)
        tracker = WifiTracker(
            [{"name": "Hour-Meng", "mac": "AA-BB-CC-DD-EE-FF", "ip": "192.168.1.42"}],
            interval=30,
        )
        present = tracker.scan_once(table)
        self.assertTrue(present[0].connected)
        self.assertTrue(tracker.is_connected("Hour-Meng"))
        self.assertEqual(presence_status(True, True), "confirmed")
        self.assertEqual(presence_status(True, False), "face_only")
        self.assertEqual(presence_status(False, True), "wifi_only")
        self.assertEqual(presence_status(False, False), "off_site")
        gone = tracker.scan_once("192.168.1.1     0x1         0x2         00:11:22:33:44:55     *        wlan0\n")
        self.assertFalse(gone[0].connected)
        dropped = tracker.departures()
        self.assertEqual(dropped, ["Hour-Meng"])


class GarageApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from launcher import DashboardRequestHandler, GLOBAL_ENGINE, ThreadingHTTPServer

        GLOBAL_ENGINE.bay_manager.set_bays(DEFAULT_BAYS)
        GLOBAL_ENGINE.bay_telemetry = GLOBAL_ENGINE.bay_manager.telemetry()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, body

    def test_telemetry_and_scorecard_under_5ms(self):
        from launcher import GLOBAL_ENGINE

        t0 = time.perf_counter()
        payload = GLOBAL_ENGINE.garage_telemetry()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertLess(elapsed_ms, 5.0)
        self.assertGreaterEqual(len(payload["bays"]), 3)
        ids = {b["bay_id"] for b in payload["bays"]}
        self.assertTrue({"bay_1", "bay_2", "tools"}.issubset(ids))
        for bay in payload["bays"]:
            self.assertIn(bay["state"], ("WORKING", "IDLE", "EMPTY"))

        status, body = self._get("/api/garage/telemetry")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("bays", data)
        self.assertIn("fps", data)

        status, body = self._get("/api/garage/scorecard")
        self.assertEqual(status, 200)
        card = json.loads(body)
        self.assertIn("technicians", card)
        self.assertIn("bays", card)
        self.assertIn("attendance_logs", card)

    def _post(self, path: str, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        conn = HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_set_bays_does_not_renumber(self):
        from launcher import GLOBAL_ENGINE

        previous = list(GLOBAL_ENGINE.cfg.get("bays") or DEFAULT_BAYS)
        remaining = [
            {"id": "bay_1", "name": "Bay 1", "roi": [0.10, 0.20, 0.35, 0.60], "type": "vehicle_bay"},
            {"id": "bay_2", "name": "Bay 2", "roi": [0.55, 0.20, 0.35, 0.60], "type": "vehicle_bay"},
            {"id": "bay_4", "name": "Bay 4", "roi": [0.10, 0.70, 0.20, 0.20], "type": "vehicle_bay"},
            {"id": "bay_5", "name": "Bay 5", "roi": [0.40, 0.70, 0.20, 0.20], "type": "vehicle_bay"},
        ]
        try:
            status, body = self._post("/api/garage/bays", {"bays": remaining})
            self.assertEqual(status, 200)
            data = json.loads(body)
            names = [bay["name"] for bay in data["bays"]]
            self.assertEqual(names, ["Bay 1", "Bay 2", "Bay 4", "Bay 5"])
            self.assertEqual(next_available_bay_name(data["bays"]), "Bay 3")
        finally:
            GLOBAL_ENGINE.set_bays(previous)


if __name__ == "__main__":
    unittest.main()
