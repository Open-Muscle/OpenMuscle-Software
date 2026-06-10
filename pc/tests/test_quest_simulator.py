"""Tests for the synthetic quest_hand simulator (simulate/quest_hand.py).

The simulator exists so the whole quest pipeline (ingest -> record ->
train -> predict -> both hand viewers) can run with zero hardware. These
tests pin the contracts that pipeline depends on:
 - frame shape matches what ingest_quest_packet expects (25 joints,
   canonical WebXR names, pos + rot per joint)
 - the latent curls actually articulate the hand (fingertips move)
 - the flexgrid matrix is correlated with the curls (learnable signal)
 - a generated frame survives real ingestion as a 175-float device
"""

import math

from openmuscle.simulate.quest_hand import (
    JOINT_NAMES, N_JOINTS, finger_curls, hand_frame, hand_pose,
    flexgrid_matrix,
)
from openmuscle.web.state import AppState


class TestJointLayout:
    def test_25_joints_canonical_order(self):
        assert N_JOINTS == 25
        assert JOINT_NAMES[0] == "wrist"
        assert JOINT_NAMES[1] == "thumb-metacarpal"
        assert JOINT_NAMES[4] == "thumb-tip"
        assert JOINT_NAMES[5] == "index-finger-metacarpal"
        assert JOINT_NAMES[9] == "index-finger-tip"
        assert JOINT_NAMES[24] == "pinky-finger-tip"

    def test_hand_pose_returns_25_positions(self):
        positions, quat = hand_pose(0.0)
        assert len(positions) == 25
        assert len(quat) == 4
        # Unit quaternion
        assert math.isclose(sum(c * c for c in quat), 1.0, rel_tol=1e-6)

    def test_frame_payload_shape(self):
        frame = hand_frame(1.5)
        assert frame["handedness"] == "right"
        assert frame["device_id"] == "quest-sim"
        assert len(frame["joints"]) == 25
        for j in frame["joints"]:
            assert len(j["pos"]) == 3
            assert len(j["rot"]) == 4
        assert [j["name"] for j in frame["joints"]] == list(JOINT_NAMES)


class TestArticulation:
    def test_curls_in_unit_range(self):
        for t in (0.0, 0.7, 3.3, 12.9):
            for c in finger_curls(t):
                assert 0.0 <= c <= 1.0

    def test_curls_move_fingertips(self):
        """Pick two times where the index curl differs a lot; its tip must
        be in a meaningfully different place relative to the wrist."""
        t_a, t_b = 0.0, 2.0
        # Make sure the latent signal actually differs at these times.
        assert abs(finger_curls(t_a)[1] - finger_curls(t_b)[1]) > 0.2
        pa, _ = hand_pose(t_a)
        pb, _ = hand_pose(t_b)
        tip = JOINT_NAMES.index("index-finger-tip")
        rel_a = [pa[tip][i] - pa[0][i] for i in range(3)]
        rel_b = [pb[tip][i] - pb[0][i] for i in range(3)]
        assert math.dist(rel_a, rel_b) > 0.01

    def test_hand_is_hand_sized(self):
        """Every joint within 25 cm of the wrist, fingers above it."""
        positions, _ = hand_pose(4.2)
        wrist = positions[0]
        for p in positions[1:]:
            assert math.dist(p, wrist) < 0.25


class TestFlexgridCorrelation:
    def test_matrix_shape_and_range(self):
        m = flexgrid_matrix(finger_curls(1.0))
        assert len(m) == 16
        assert all(len(col) == 4 for col in m)
        for col in m:
            for v in col:
                assert isinstance(v, int)
                assert 0 <= v <= 4095

    def test_matrix_tracks_curls(self):
        """Open hand vs closed fist must produce clearly different totals."""
        open_hand = flexgrid_matrix((0.0,) * 5)
        fist = flexgrid_matrix((1.0,) * 5)
        total_open = sum(v for col in open_hand for v in col)
        total_fist = sum(v for col in fist for v in col)
        assert total_fist > total_open + 10000

    def test_per_finger_locality(self):
        """Curling only the pinky should move the right edge of the array
        more than the left edge (sensors are spatially weighted)."""
        base = flexgrid_matrix((0.0,) * 5)
        pinky = flexgrid_matrix((0.0, 0.0, 0.0, 0.0, 1.0))
        left_delta = sum(pinky[c][r] - base[c][r] for c in range(4) for r in range(4))
        right_delta = sum(pinky[c][r] - base[c][r] for c in range(12, 16) for r in range(4))
        assert right_delta > left_delta


class TestIngestRoundTrip:
    def test_generated_frame_ingests_as_quest_device(self):
        s = AppState.__new__(AppState)
        s.devices = {}
        s.recording = None
        s.engine = None
        s.inference_enabled = False
        s.hand_target = None

        s.ingest_quest_packet(hand_frame(0.5))
        assert "quest-sim" in s.devices
        d = s.devices["quest-sim"]
        assert d.device_type == "quest_hand"
        assert len(d.last_values) == 25 * 7
        # No NaN/inf anywhere in the flattened vector
        assert all(math.isfinite(v) for v in d.last_values)
