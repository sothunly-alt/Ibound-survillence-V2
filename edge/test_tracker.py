"""Multi-object tracking and body ReID identity persistence."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from person import Detection, standing_person_keypoints
from reid import BodyReIDExtractor, appearance_embedding
from runtime import (
    DEFAULT_KPT_CONF,
    EDGE_WEIGHTS,
    person_weights_name,
    resolve_kpt_conf,
    resolve_runtime,
)
from tracker import PersonTracker, Track


def _det(
    *,
    x1=80,
    y1=40,
    x2=160,
    y2=280,
    name=None,
    staff=False,
    feat=None,
) -> Detection:
    det = Detection(x1, y1, x2, y2, 0.9, standing_person_keypoints())
    det.accepted = True
    det.identity = name
    det.is_staff = staff
    det.identity_conf = 0.9 if staff else 0.0
    det.reid_feat = feat
    return det


class RuntimeProfileTests(unittest.TestCase):
    def test_cpu_profile(self):
        profile = resolve_runtime({"runtime": "cpu", "weights": "yolo11n-pose.pt"})
        self.assertEqual(profile.name, "cpu")
        self.assertEqual(profile.yolo_device, "cpu")
        self.assertTrue(profile.reid_enabled)
        self.assertGreaterEqual(profile.track_min_hits, 2)

    def test_person_weights_never_use_detect_checkpoint(self):
        self.assertIn("pose", EDGE_WEIGHTS)
        self.assertEqual(person_weights_name("yolo11n_improved.pt"), "yolo11n-pose.pt")
        self.assertEqual(person_weights_name("yolo11n.pt"), "yolo11n-pose.pt")
        self.assertEqual(person_weights_name("yolo11s-pose.pt"), "yolo11s-pose.pt")
        profile = resolve_runtime({"runtime": "cpu", "weights": "yolo11n_improved.pt"})
        self.assertEqual(profile.weights_name, "yolo11n-pose.pt")

    def test_default_kpt_conf_matches_anatomy_helpers(self):
        self.assertGreaterEqual(DEFAULT_KPT_CONF, 0.35)
        self.assertGreaterEqual(resolve_kpt_conf({}), 0.35)

    def test_cuda_falls_back_without_gpu(self):
        profile = resolve_runtime({"runtime": "cuda"})
        self.assertIn(profile.name, ("cuda", "cpu"))
        if profile.name == "cpu":
            self.assertEqual(profile.yolo_device, "cpu")


class TrackerIdentityTests(unittest.TestCase):
    def test_identity_survives_face_miss(self):
        tracker = PersonTracker(max_age=10, min_hits=3, iou_threshold=0.3)
        confirmed = []
        for _ in range(3):
            confirmed = tracker.update([_det(name="George", staff=True)])
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0].identity, "George")
        self.assertTrue(confirmed[0].is_staff)
        track_id = confirmed[0].track_id

        turned = tracker.update([_det(name="Employee", staff=False)])
        self.assertEqual(len(turned), 1)
        self.assertEqual(turned[0].identity, "George")
        self.assertTrue(turned[0].is_staff)
        self.assertEqual(turned[0].track_id, track_id)

    def test_two_people_keep_separate_ids(self):
        tracker = PersonTracker(max_age=10, min_hits=2, iou_threshold=0.3)
        for _ in range(2):
            tracker.update(
                [
                    _det(x1=80, y1=40, x2=160, y2=280, name="George", staff=True),
                    _det(x1=400, y1=40, x2=480, y2=280, name="Alex", staff=True),
                ]
            )
        out = tracker.update(
            [
                _det(x1=82, y1=42, x2=162, y2=278, name="Employee", staff=False),
                _det(x1=398, y1=38, x2=482, y2=282, name="Employee", staff=False),
            ]
        )
        names = sorted(d.identity for d in out)
        self.assertEqual(names, ["Alex", "George"])
        self.assertEqual(len({d.track_id for d in out}), 2)

    def test_unconfirmed_tracks_are_withheld(self):
        tracker = PersonTracker(max_age=10, min_hits=3, iou_threshold=0.3)
        out = tracker.update([_det(name="George", staff=True)])
        self.assertEqual(out, [])
        missed = tracker.update([])
        self.assertEqual(missed, [])

    def test_confirmed_track_coasts_on_miss(self):
        tracker = PersonTracker(max_age=10, min_hits=3, iou_threshold=0.3)
        confirmed = []
        for _ in range(3):
            confirmed = tracker.update([_det(name="George", staff=True)])
        self.assertEqual(len(confirmed), 1)
        track_id = confirmed[0].track_id
        coasted = tracker.update([])
        self.assertEqual(len(coasted), 1)
        self.assertEqual(coasted[0].identity, "George")
        self.assertTrue(coasted[0].is_staff)
        self.assertEqual(coasted[0].track_id, track_id)
        for _ in range(11):
            coasted = tracker.update([])
        self.assertEqual(coasted, [])

    def test_coasted_track_does_not_keep_skeleton(self):
        tracker = PersonTracker(max_age=10, min_hits=3, iou_threshold=0.3)
        confirmed = []
        for _ in range(3):
            confirmed = tracker.update([_det(name="George", staff=True)])
        self.assertTrue(confirmed[0].keypoints)
        coasted = tracker.update([])
        self.assertEqual(len(coasted), 1)
        self.assertTrue(coasted[0].coasting)
        self.assertEqual(coasted[0].keypoints, [])
        self.assertEqual(coasted[0].identity, "George")

    def test_static_low_conf_unknown_is_clutter(self):
        tracker = PersonTracker(max_age=10, min_hits=3, iou_threshold=0.3, static_hits=20)
        out = []
        for _ in range(8):
            det = _det(name=None, staff=False)
            det.conf = 0.40
            out = tracker.update([det])
        self.assertEqual(out, [])
        self.assertFalse(any(t.hits >= tracker.min_hits for t in tracker.tracks))

    def test_static_high_conf_unknown_is_clutter(self):
        tracker = PersonTracker(max_age=10, min_hits=3, iou_threshold=0.3, static_hits=20)
        out = []
        for _ in range(8):
            det = _det(name=None, staff=False)
            det.conf = 0.75
            out = tracker.update([det])
        self.assertEqual(out, [])
        self.assertFalse(any(t.hits >= tracker.min_hits for t in tracker.tracks))

    def test_moving_high_conf_unknown_is_kept(self):
        tracker = PersonTracker(max_age=10, min_hits=3, iou_threshold=0.3, static_hits=20)
        out = []
        for i in range(8):
            det = _det(x1=80 + i * 12, y1=40, x2=160 + i * 12, y2=280, name=None, staff=False)
            det.conf = 0.75
            out = tracker.update([det])
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0].coasting)

    def test_static_staff_is_not_cluttered_by_low_conf(self):
        tracker = PersonTracker(max_age=10, min_hits=3, iou_threshold=0.3, static_hits=20)
        out = []
        for _ in range(8):
            det = _det(name="George", staff=True)
            det.conf = 0.40
            out = tracker.update([det])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].identity, "George")

    def test_shrunk_box_stays_on_same_track(self):
        tracker = PersonTracker(max_age=10, min_hits=2, iou_threshold=0.3)
        out = []
        for _ in range(2):
            out = tracker.update([_det(x1=80, y1=40, x2=160, y2=280, name="George", staff=True)])
        track_id = out[0].track_id
        shrunk = tracker.update([_det(x1=100, y1=180, x2=150, y2=250, name="Employee", staff=False)])
        self.assertEqual(len(shrunk), 1)
        self.assertEqual(shrunk[0].track_id, track_id)
        self.assertEqual(shrunk[0].identity, "George")


class ReIDEmbeddingTests(unittest.TestCase):
    def test_fallback_embedding_is_stable_and_discriminative(self):
        extractor = BodyReIDExtractor(model_path=None)
        red = np.zeros((240, 120, 3), dtype=np.uint8)
        red[20:220, 20:100] = (40, 40, 200)
        blue = np.zeros((240, 120, 3), dtype=np.uint8)
        blue[20:220, 20:100] = (200, 40, 40)
        feat_a = extractor.extract(red, (20, 20, 100, 220))
        feat_b = extractor.extract(red, (22, 18, 98, 218))
        feat_c = extractor.extract(blue, (20, 20, 100, 220))
        self.assertEqual(feat_a.shape[0], 512)
        self.assertGreater(BodyReIDExtractor.cosine_similarity(feat_a, feat_b), 0.90)
        self.assertGreater(
            BodyReIDExtractor.cosine_similarity(feat_a, feat_b),
            BodyReIDExtractor.cosine_similarity(feat_a, feat_c),
        )

    def test_gallery_rebinds_after_new_track(self):
        tracker = PersonTracker(max_age=10, min_hits=2, iou_threshold=0.3, reid_threshold=0.50)
        extractor = BodyReIDExtractor(model_path=None)
        red = np.zeros((240, 120, 3), dtype=np.uint8)
        red[20:220, 20:100] = (30, 80, 210)
        feat = extractor.extract(red, (20, 20, 100, 220))
        tracker.update([_det(name="George", staff=True, feat=feat)])
        tracker.update([_det(name="George", staff=True, feat=feat)])
        tracker.reset()
        tracker.gallery.remember("George", feat)
        revived = None
        for _ in range(2):
            revived = tracker.update(
                [_det(x1=300, y1=40, x2=380, y2=280, name="Employee", staff=False, feat=feat)]
            )
        self.assertEqual(len(revived), 1)
        self.assertEqual(revived[0].identity, "George")
        self.assertTrue(revived[0].is_staff)

    def test_appearance_embedding_rejects_empty_crop(self):
        zeros = appearance_embedding(np.zeros((4, 4, 3), dtype=np.uint8))
        self.assertEqual(float(np.linalg.norm(zeros)), 0.0)


class LivenessAndNegativesTests(unittest.TestCase):
    def test_box_motion_energy_separates_static_from_moving(self):
        from liveness import DEAD_MOTION_ENERGY, box_motion_energy, to_probe_gray

        still = np.zeros((120, 120, 3), dtype=np.uint8)
        still[20:80, 20:80] = 80
        moved = still.copy()
        moved[20:80, 20:80] = 200
        gray_a = to_probe_gray(still)
        gray_b = to_probe_gray(moved)
        self.assertLess(box_motion_energy(gray_a, gray_a, (20, 20, 80, 80)), DEAD_MOTION_ENERGY)
        self.assertGreater(box_motion_energy(gray_b, gray_a, (20, 20, 80, 80)), DEAD_MOTION_ENERGY)

    def test_bank_hard_negatives_writes_crop(self):
        from negatives import bank_hard_negatives

        tmp = tempfile.TemporaryDirectory()
        try:
            frame = np.zeros((120, 120, 3), dtype=np.uint8)
            frame[20:80, 20:80] = 80
            trk = Track(track_id=7, bbox=(20, 20, 80, 80), conf=0.75)
            paths = bank_hard_negatives(frame, [trk], Path(tmp.name))
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].is_file())
            self.assertTrue(paths[0].with_suffix(".json").is_file())
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
