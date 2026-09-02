"""Kinematic human validation: reject shoes, bags, and other desk clutter."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from occupancy import partial_legs_pose_keypoints, under_vehicle_pose_keypoints
from person import (
    backpack_clutter_keypoints,
    closeup_face_keypoints,
    is_creeper_or_underbody_pose,
    is_human_pose,
    shoe_pair_keypoints,
    standing_person_keypoints,
)


class KinematicPoseTests(unittest.TestCase):
    def test_standing_person_is_accepted(self):
        kpts = standing_person_keypoints()
        self.assertTrue(is_human_pose(50, 10, 150, 280, kpts, 480, kpt_conf=0.35))

    def test_closeup_face_is_accepted(self):
        kpts = closeup_face_keypoints()
        self.assertTrue(is_human_pose(40, 20, 160, 200, kpts, 480, kpt_conf=0.35))

    def test_shoe_pair_on_desk_is_rejected(self):
        kpts = shoe_pair_keypoints()
        self.assertFalse(
            is_creeper_or_underbody_pose(60, 140, 200, 280, kpts, 480, kpt_conf=0.35)
        )
        self.assertFalse(is_human_pose(60, 140, 200, 280, kpts, 480, kpt_conf=0.35))

    def test_backpack_clutter_is_rejected(self):
        kpts = backpack_clutter_keypoints()
        self.assertFalse(is_human_pose(40, 40, 180, 260, kpts, 480, kpt_conf=0.35))

    def test_under_vehicle_connected_chain_is_accepted(self):
        kpts = under_vehicle_pose_keypoints()
        self.assertTrue(
            is_creeper_or_underbody_pose(40, 70, 280, 160, kpts, 480, kpt_conf=0.35)
        )
        self.assertTrue(is_human_pose(40, 70, 280, 160, kpts, 480, kpt_conf=0.35))

    def test_partial_legs_with_hips_are_accepted(self):
        kpts = partial_legs_pose_keypoints()
        self.assertTrue(
            is_creeper_or_underbody_pose(100, 80, 280, 160, kpts, 480, kpt_conf=0.35)
        )

    def test_two_floating_ankles_are_rejected(self):
        kpts = [(0.0, 0.0, 0.0)] * 17
        kpts[15] = (90.0, 200.0, 0.45)
        kpts[16] = (130.0, 202.0, 0.44)
        self.assertFalse(is_human_pose(70, 160, 160, 240, kpts, 480, kpt_conf=0.35))


if __name__ == "__main__":
    unittest.main()
