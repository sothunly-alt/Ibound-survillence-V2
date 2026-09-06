"""Tests for Tier 2 Vision AI Auditor, Token Gating, and Bay Integration."""

from __future__ import annotations

import base64
import sys
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_auditor import (
    AIAuditVerdict,
    AIAuditorQueue,
    FireworksVLMClient,
    TokenSaverGate,
    extract_dual_crops,
)
from db import connect, get_recent_ai_audits, record_ai_audit_verdict
from occupancy import BayZoneManager, is_phone_usage_pose, is_sitting_pose
from person import Detection


class TestAIAuditor(unittest.TestCase):
    def test_token_saver_gate_duration_and_cooldown(self):
        gate = TokenSaverGate(duration_threshold=5.0, cooldown_seconds=180.0)
        bay_id = "bay_1"

        # Frame at t=0: Pattern starts, but duration < 5s -> Should NOT trigger
        self.assertFalse(gate.evaluate(bay_id, pattern_matched=True, now=100.0))

        # Frame at t=3s: Duration is 3s < 5s -> Should NOT trigger
        self.assertFalse(gate.evaluate(bay_id, pattern_matched=True, now=103.0))

        # Frame at t=5.1s: Duration >= 5s -> Should TRIGGER
        self.assertTrue(gate.evaluate(bay_id, pattern_matched=True, now=105.1))

        # Frame at t=6.0s: In cooldown (< 180s) -> Should NOT trigger again
        self.assertFalse(gate.evaluate(bay_id, pattern_matched=True, now=106.0))
        self.assertFalse(gate.evaluate(bay_id, pattern_matched=True, now=200.0))

        # Frame at t=286s (181s after last audit): Duration >= 5s -> Should TRIGGER again
        self.assertFalse(gate.evaluate(bay_id, pattern_matched=True, now=280.0))  # Start new pattern
        self.assertTrue(gate.evaluate(bay_id, pattern_matched=True, now=286.0))  # 6s later

    def test_dual_crop_extraction(self):
        # Create a dummy 640x480 frame
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.rectangle(frame, (100, 100), (300, 400), (255, 255, 255), -1)

        # 17 COCO keypoints with wrists near center
        kpts = [(0.0, 0.0, 0.0)] * 17
        kpts[9] = (200.0, 250.0, 0.9)   # L_WRIST
        kpts[10] = (220.0, 250.0, 0.9)  # R_WRIST

        b64_h, b64_c, hand_img, context_img = extract_dual_crops(
            frame, keypoints=kpts, bbox=(100, 100, 300, 400)
        )

        self.assertTrue(len(b64_h) > 50)
        self.assertTrue(len(b64_c) > 50)
        self.assertGreater(hand_img.shape[0], 10)
        self.assertGreater(context_img.shape[0], 10)

    def test_db_audit_logging(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = Path(tf.name)
        conn = connect(db_path)

        record_ai_audit_verdict(
            conn,
            bay_id="Bay-1",
            technician_name="Alice",
            action="DIAGNOSTIC_TOOL",
            category="ACTIVE_WORK",
            confidence=0.95,
            explanation="Technician is using an OBD-II scanner.",
        )

        records = get_recent_ai_audits(conn, bay_id="Bay-1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action"], "DIAGNOSTIC_TOOL")
        self.assertEqual(records[0]["category"], "ACTIVE_WORK")
        self.assertAlmostEqual(records[0]["confidence"], 0.95)
        conn.close()

    def test_bay_zone_manager_with_ai_auditor_override(self):
        # Mock VLM client returning diagnostic tool (work activity)
        class MockDiagnosticVLM(FireworksVLMClient):
            def audit_activity(self, *args, **kwargs):
                return AIAuditVerdict(
                    bay_id="bay_1",
                    technician_id="Bob",
                    action="DIAGNOSTIC_TOOL",
                    category="ACTIVE_WORK",
                    confidence=0.98,
                    explanation="Using OBD tool.",
                    is_work_activity=True,
                )

        gate = TokenSaverGate(duration_threshold=0.1, cooldown_seconds=10.0)
        auditor = AIAuditorQueue(vlm_client=MockDiagnosticVLM(), gate=gate)

        bays_cfg = [
            {"id": "bay_1", "name": "Bay 1", "type": "vehicle_bay", "roi": [0.0, 0.0, 1.0, 1.0]}
        ]
        mgr = BayZoneManager(bays_cfg, ai_auditor=auditor)

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Create a detection with pose that looks like holding something
        kpts = [(0.0, 0.0, 0.0)] * 17
        kpts[5] = (200.0, 150.0, 0.9)  # L_SHOULDER
        kpts[6] = (250.0, 150.0, 0.9)  # R_SHOULDER
        kpts[9] = (220.0, 200.0, 0.9)  # L_WRIST
        kpts[10] = (230.0, 200.0, 0.9) # R_WRIST
        kpts[0] = (225.0, 140.0, 0.9)  # NOSE

        det = Detection(
            x1=150, y1=100, x2=300, y2=400, conf=0.9, accepted=True,
            identity="Bob", is_staff=True, keypoints=kpts
        )

        # Update manager
        mgr.update([det], 640, 480, now=10.0, frame=frame)
        time.sleep(0.1)  # Allow async worker to process

        mgr.update([det], 640, 480, now=11.0, frame=frame)
        snaps = mgr.snapshots()
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0].state, "WORKING")

        auditor.shutdown()


if __name__ == "__main__":
    unittest.main()
