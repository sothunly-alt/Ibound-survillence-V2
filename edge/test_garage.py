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
    complete_vehicle_job,
    connect,
    get_daily_garage_summary,
    get_or_create_vehicle_job,
    get_vehicle_job_history,
    list_vehicle_jobs,
    record_face_clock_in,
    record_face_clock_out,
    update_technician_activity,
    update_vehicle_job_activity,
)
from occupancy import (
    DEFAULT_BAYS,
    BayZoneManager,
    crouching_pose_keypoints,
    idle_standing_keypoints,
    is_under_vehicle_pose,
    is_working_pose,
    next_available_bay_name,
    normalize_bays,
    partial_legs_pose_keypoints,
    point_in_polygon,
    point_in_roi,
    roi_as_polygon,
    under_vehicle_pose_keypoints,
    working_pose_keypoints,
)
from person import Detection
from report import build_garage_report, efficiency_badge
from sensors.wifi_tracker import WifiTracker, parse_arp_table, presence_status
from service_patterns import (
    KNOWLEDGE_BASE,
    calculate_performance_grade,
    evaluate_completed_vehicle_job,
)
from vehicle import VehicleDetection, extract_vehicle_detections


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
        self.assertTrue(is_under_vehicle_pose(under_vehicle_pose_keypoints()))
        self.assertTrue(is_under_vehicle_pose(partial_legs_pose_keypoints()))
        self.assertFalse(is_under_vehicle_pose(idle_standing_keypoints()))

    def test_under_vehicle_occlusion_and_dwell_grace_period(self):
        manager = BayZoneManager(
            DEFAULT_BAYS,
            under_car_grace_seconds=5.0,
            break_timeout_seconds=60.0,
            occupy_confirm_seconds=0.01,
            occupy_clear_seconds=0.01,
        )
        work = _det(_shift_kpts(under_vehicle_pose_keypoints(), 80, 280), name="Hour-Meng")
        t0 = 100.0

        # 1. Mechanic starts working under vehicle (horizontal pose inside bay)
        manager.update([work], 1000, 1000, t0, kpt_conf=0.4)
        manager.update([work], 1000, 1000, t0 + 2.0, kpt_conf=0.4)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertEqual(snaps["bay_1"].state, "UNDER_VEHICLE")
        self.assertEqual(snaps["bay_1"].mechanic_name, "Hour-Meng")
        self.assertGreaterEqual(snaps["bay_1"].wrench_seconds, 1.9)
        self.assertGreaterEqual(snaps["bay_1"].under_vehicle_seconds, 1.9)

        # 2. Mechanic steps away from bay -> immediately switches to ON_BREAK and pauses work timer
        manager.update([], 1000, 1000, t0 + 5.0, kpt_conf=0.4)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertEqual(snaps["bay_1"].state, "ON_BREAK")
        self.assertEqual(snaps["bay_1"].mechanic_name, "Hour-Meng")
        # Wrench time did not increase during break
        self.assertAlmostEqual(snaps["bay_1"].wrench_seconds, 2.0, places=1)

    def test_break_and_resume_continuity(self):
        manager = BayZoneManager(
            DEFAULT_BAYS,
            under_car_grace_seconds=5.0,
            break_timeout_seconds=60.0,
            occupy_confirm_seconds=0.01,
            occupy_clear_seconds=0.01,
        )
        work = _det(_shift_kpts(working_pose_keypoints(), 80, 280), name="Hour-Meng")
        t0 = 100.0
        manager.update([work], 1000, 1000, t0, kpt_conf=0.4)
        manager.update([work], 1000, 1000, t0 + 2.0, kpt_conf=0.4)
        wrench_before_break = {s.bay_id: s.wrench_seconds for s in manager.snapshots()}["bay_1"]
        self.assertGreater(wrench_before_break, 1.5)

        # Mechanic steps away past under_car_grace_seconds (5s) -> transitions to ON_BREAK
        manager.update([], 1000, 1000, t0 + 10.0, kpt_conf=0.4)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertEqual(snaps["bay_1"].state, "ON_BREAK")
        self.assertEqual(snaps["bay_1"].mechanic_name, "Hour-Meng")

        # Mechanic returns to bay -> resumes WORKING without resetting wrench_seconds
        manager.update([work], 1000, 1000, t0 + 15.0, kpt_conf=0.4)
        manager.update([work], 1000, 1000, t0 + 17.0, kpt_conf=0.4)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertEqual(snaps["bay_1"].state, "WORKING")
        self.assertEqual(snaps["bay_1"].mechanic_name, "Hour-Meng")
        self.assertGreater(snaps["bay_1"].wrench_seconds, wrench_before_break + 1.5)

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
        empty = manager.update([], 1000, 1000, t0 + 3700.0, kpt_conf=0.4)
        by_id = {s.bay_id: s.state for s in empty}
        self.assertEqual(by_id["bay_1"], "EMPTY")
        self.assertEqual(by_id["bay_2"], "EMPTY")
        self.assertEqual(by_id["tools"], "EMPTY")

    def test_tool_station_time_counting(self):
        manager = BayZoneManager(
            DEFAULT_BAYS,
            break_timeout_seconds=60.0,
            occupy_confirm_seconds=0.01,
            occupy_clear_seconds=0.01,
        )
        # Position a worker inside the Tool Station box (x=450, y=100)
        tool_worker = _det(_shift_kpts(idle_standing_keypoints(), 430, 60), name="Hour-Meng", x1=430, y1=60, x2=530, y2=220)
        t0 = 100.0

        # Worker is in Tool Station -> counts time!
        manager.update([tool_worker], 1000, 1000, t0, kpt_conf=0.4)
        manager.update([tool_worker], 1000, 1000, t0 + 1.0, kpt_conf=0.4)
        manager.update([tool_worker], 1000, 1000, t0 + 2.0, kpt_conf=0.4)
        manager.update([tool_worker], 1000, 1000, t0 + 3.0, kpt_conf=0.4)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertEqual(snaps["tools"].state, "WORKING")
        self.assertEqual(snaps["tools"].mechanic_name, "Hour-Meng")
        self.assertGreaterEqual(snaps["tools"].wrench_seconds, 2.9)

        # Worker steps away from Tool Station -> pauses time!
        manager.update([], 1000, 1000, t0 + 4.0, kpt_conf=0.4)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertEqual(snaps["tools"].state, "ON_BREAK")
        self.assertAlmostEqual(snaps["tools"].wrench_seconds, 3.0, places=1)

    def test_multiple_employees_in_same_bay(self):
        manager = BayZoneManager(
            DEFAULT_BAYS,
            break_timeout_seconds=60.0,
            occupy_confirm_seconds=0.01,
            occupy_clear_seconds=0.01,
        )
        # 2 workers inside Bay 1: Hour-Meng and Sothun
        w1 = _det(_shift_kpts(working_pose_keypoints(), 50, 250), name="Hour-Meng")
        w2 = _det(_shift_kpts(working_pose_keypoints(), 120, 300), name="Sothun")
        t0 = 100.0

        manager.update([w1, w2], 1000, 1000, t0, kpt_conf=0.4)
        manager.update([w1, w2], 1000, 1000, t0 + 1.0, kpt_conf=0.4)
        manager.update([w1, w2], 1000, 1000, t0 + 2.0, kpt_conf=0.4)
        snaps = {s.bay_id: s for s in manager.snapshots()}

        # Verify both technicians are tracked
        self.assertEqual(snaps["bay_1"].state, "WORKING")
        self.assertIn("Hour-Meng", snaps["bay_1"].mechanic_name)
        self.assertIn("Sothun", snaps["bay_1"].mechanic_name)
        self.assertIn("Hour-Meng", snaps["bay_1"].technicians_times)
        self.assertIn("Sothun", snaps["bay_1"].technicians_times)
        self.assertGreaterEqual(snaps["bay_1"].technicians_times["Hour-Meng"], 1.9)
        self.assertGreaterEqual(snaps["bay_1"].technicians_times["Sothun"], 1.9)

        # Check the formatted badge includes both names and their individual times
        badge = snaps["bay_1"].as_dict()["badge"]
        self.assertIn("Hour-Meng", badge)
        self.assertIn("Sothun", badge)

    def test_auto_vehicle_detection_and_dynamic_bay_sync(self):
        manager = BayZoneManager(
            [],  # Empty initial manual bays
            break_timeout_seconds=60.0,
            occupy_confirm_seconds=0.01,
            occupy_clear_seconds=0.01,
        )
        # Simulate auto-detected vehicle at x=200..700, y=300..800
        car = VehicleDetection(
            x1=200.0,
            y1=300.0,
            x2=700.0,
            y2=800.0,
            conf=0.88,
            vehicle_type="car",
            vehicle_id="auto_car_1",
        )
        manager.sync_auto_vehicles([car], frame_w=1000, frame_h=1000)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertIn("auto_car_1", snaps)
        self.assertEqual(snaps["auto_car_1"].name, "Auto: Car #1")
        self.assertEqual(snaps["auto_car_1"].type, "vehicle_bay")

        # Now simulate a technician working near/under that auto-detected car
        tech = _det(_shift_kpts(working_pose_keypoints(), 350, 450), name="Hour-Meng")
        t0 = 100.0
        manager.update([tech], 1000, 1000, t0, kpt_conf=0.4)
        manager.update([tech], 1000, 1000, t0 + 1.0, kpt_conf=0.4)
        manager.update([tech], 1000, 1000, t0 + 2.0, kpt_conf=0.4)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertEqual(snaps["auto_car_1"].state, "WORKING")
        self.assertEqual(snaps["auto_car_1"].mechanic_name, "Hour-Meng")
        self.assertGreaterEqual(snaps["auto_car_1"].wrench_seconds, 1.9)

        # Now simulate the car driving away / departing after work is done
        departed = manager.sync_auto_vehicles([], 1000, 1000, now=t0 + 20.0)
        self.assertIn("auto_car_1", departed)
        snaps = {s.bay_id: s for s in manager.snapshots()}
        self.assertEqual(snaps["auto_car_1"].state, "EMPTY")

    def test_vehicle_service_performance_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "test_eval.db")
            job_id = "JOB-auto_car_1-20260902-01"
            now_str = datetime.now().isoformat(timespec="seconds")

            # Create a completed vehicle job with 1200s active, 200s break
            conn.execute(
                """
                INSERT INTO vehicle_jobs (
                    job_id, bay_id, vehicle_type, primary_technician,
                    status, total_active_seconds, total_break_seconds,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, "auto_car_1", "car", "Hour-Meng", "COMPLETED", 1200.0, 200.0, now_str, now_str, now_str),
            )
            # Add daily technician log
            conn.execute(
                """
                INSERT INTO daily_vehicle_job_logs (job_id, day, technician_name, active_seconds, break_seconds)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, "2026-09-02", "Hour-Meng", 1200.0, 200.0),
            )
            conn.commit()

            report = evaluate_completed_vehicle_job(conn, job_id)
            self.assertIsNotNone(report)
            self.assertEqual(report.job_id, job_id)
            self.assertEqual(report.primary_technician, "Hour-Meng")
            self.assertEqual(report.performance_grade, "A+")
            self.assertGreaterEqual(report.performance_score, 90)
            self.assertAlmostEqual(report.efficiency_pct, (1200.0 / 1400.0) * 100.0, places=1)
            self.assertIn("Hour-Meng", report.technicians_breakdown)

            # Test Knowledge Base
            self.assertIsNotNone(KNOWLEDGE_BASE.get_template("oil_change"))
            brake_tmpl = KNOWLEDGE_BASE.get_template("brake_rotor_and_pad_replacement")
            self.assertIsNotNone(brake_tmpl)
            self.assertEqual(len(brake_tmpl["stages"]), 7)
            self.assertEqual(brake_tmpl["target_minutes"], 35.0)
            self.assertIn("12mm socket & ratchet", brake_tmpl["tools_required"])
            conn.close()

    def test_parked_waiting_queue_time_and_work_transition(self):
        manager = BayZoneManager(
            DEFAULT_BAYS,
            occupy_confirm_seconds=0.01,
            occupy_clear_seconds=0.01,
        )
        t0 = 100.0
        # 1. Car is in Bay 1 (vehicle_present=True), but no mechanic is inside (customer check-in)
        manager._bays[0].vehicle_present = True
        manager.update([], 1000, 1000, t0, kpt_conf=0.4)
        manager.update([], 1000, 1000, t0 + 10.0, kpt_conf=0.4)
        
        bay1 = {s.bay_id: s for s in manager.snapshots()}["bay_1"]
        self.assertEqual(bay1.state, "PARKED_WAITING")
        self.assertAlmostEqual(bay1.queue_seconds, 2.0, places=1)
        self.assertEqual(bay1.wrench_seconds, 0.0) # Work timer stays frozen at 0!

        # 2. Mechanic enters and starts wrenching
        work_det = _det(_shift_kpts(working_pose_keypoints(), 80, 280), name="Hour-Meng")
        manager.update([work_det], 1000, 1000, t0 + 11.0, kpt_conf=0.4)
        manager.update([work_det], 1000, 1000, t0 + 12.0, kpt_conf=0.4)

        bay1_active = {s.bay_id: s for s in manager.snapshots()}["bay_1"]
        self.assertEqual(bay1_active.state, "WORKING")
        self.assertGreater(bay1_active.wrench_seconds, 0.0)
        self.assertIn("Hour-Meng", bay1_active.mechanic_name)

    def test_head_turn_keeps_locked_technician_timer(self):
        manager = BayZoneManager(
            DEFAULT_BAYS,
            occupy_confirm_seconds=0.01,
            occupy_clear_seconds=0.01,
        )
        work = _det(_shift_kpts(working_pose_keypoints(), 80, 280), name="George")
        t0 = 20.0
        manager.update([work], 1000, 1000, t0, kpt_conf=0.4)
        manager.update([work], 1000, 1000, t0 + 1.0, kpt_conf=0.4)
        turned = _det(
            _shift_kpts(working_pose_keypoints(), 80, 280),
            name="Employee",
            staff=False,
        )
        manager.update([turned], 1000, 1000, t0 + 2.0, kpt_conf=0.4)
        snap = {s.bay_id: s for s in manager.snapshots()}["bay_1"]
        self.assertEqual(snap.state, "WORKING")
        self.assertEqual(snap.mechanic_name, "George")
        self.assertIn("George", snap.technicians_times)
        self.assertNotIn("Employee", snap.technicians_times)


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

    def test_vehicle_jobs_crud_and_multi_day_aggregation(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "events.db")
            day1 = datetime(2026, 9, 1, 9, 0, 0)
            day2 = datetime(2026, 9, 2, 9, 0, 0)

            # 1. Create a vehicle job for Bay 1
            job_id = get_or_create_vehicle_job(
                conn,
                bay_id="bay_1",
                vehicle_type="truck",
                vehicle_label="Ford F-150 (Transmission)",
                primary_technician="Hour-Meng",
                timestamp=day1,
            )
            self.assertTrue(job_id.startswith("JOB-bay_1-20260901-"))

            # 2. Day 1: 3.5 hours active work (12,600s) + 30m break (1800s)
            update_vehicle_job_activity(
                conn,
                job_id=job_id,
                active_dt=12600.0,
                break_dt=1800.0,
                technician_name="Hour-Meng",
                timestamp=day1,
                status="WORKING",
            )

            # 3. Day 2: 4.2 hours active work (15,120s)
            update_vehicle_job_activity(
                conn,
                job_id=job_id,
                active_dt=15120.0,
                break_dt=0.0,
                technician_name="Hour-Meng",
                timestamp=day2,
                status="UNDER_VEHICLE",
            )

            # 4. Fetch job history across multiple days
            history = get_vehicle_job_history(conn, job_id)
            self.assertIsNotNone(history)
            self.assertEqual(history["bay_id"], "bay_1")
            self.assertEqual(history["vehicle_type"], "truck")
            self.assertEqual(len(history["daily_logs"]), 2)

            # Total = 12600 + 15120 = 27720s (7.7 hours)
            self.assertAlmostEqual(history["total_active_hours"], 7.7, places=1)
            self.assertEqual(history["daily_logs"][0]["day"], "2026-09-01")
            self.assertEqual(history["daily_logs"][0]["active_hours"], 3.5)
            self.assertEqual(history["daily_logs"][1]["day"], "2026-09-02")
            self.assertEqual(history["daily_logs"][1]["active_hours"], 4.2)

            # 5. Complete job
            ok = complete_vehicle_job(conn, job_id, timestamp=day2)
            self.assertTrue(ok)
            jobs = list_vehicle_jobs(conn, status="COMPLETED")
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "COMPLETED")
            self.assertIsNotNone(jobs[0]["completed_at"])
            conn.close()

    def test_vehicle_jobs_api(self):
        status, body = self._get("/api/garage/jobs")
        self.assertEqual(status, 200)
        jobs = json.loads(body)
        self.assertIsInstance(jobs, list)

        # Complete non-existent job returns ok=False
        status, body = self._post("/api/garage/jobs/complete", {"job_id": "NON-EXISTENT"})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertFalse(data["ok"])


if __name__ == "__main__":
    unittest.main()

