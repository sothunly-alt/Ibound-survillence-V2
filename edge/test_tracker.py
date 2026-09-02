"""Multi-object tracking and body ReID identity persistence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from person import Detection, standing_person_keypoints
from reid import BodyReIDExtractor, appearance_embedding
from runtime import resolve_runtime
from tracker import PersonTracker


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


if __name__ == "__main__":
    unittest.main()
