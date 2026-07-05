"""The per-hand controller channel split must cleanly partition the 21 label
columns: left and right disjoint, Guide (center) excluded from both."""

from openmuscle import gamepad_channels as gc


def test_channels_cover_all_21_label_indices():
    idx = sorted(c["i"] for c in gc.CHANNELS)
    assert idx == list(range(21))


def test_left_right_disjoint_and_center_excluded():
    left = set(gc.label_indices_for_hand("left"))
    right = set(gc.label_indices_for_hand("right"))
    assert left.isdisjoint(right)
    center = {c["i"] for c in gc.CHANNELS if c["hand"] == "center"}
    assert center == {20}                       # Guide, driven by neither forearm
    assert left | right | center == set(range(21))


def test_hand_assignment_matches_physical_layout():
    left = set(gc.label_indices_for_hand("left"))
    right = set(gc.label_indices_for_hand("right"))
    # Left stick (0,1), LB (8), LT (10), D-pad (16-19) are left-hand.
    assert {0, 1, 8, 10, 16, 17, 18, 19} <= left
    # Right stick (2,3), A/B/X/Y (4-7), RB (9), RT (11) are right-hand.
    assert {2, 3, 4, 5, 6, 7, 9, 11} <= right


def test_analog_channels_are_sticks_and_triggers():
    analog = [c["name"] for c in gc.CHANNELS if gc.is_analog(c)]
    assert set(analog) == {"LS_x", "LS_y", "RS_x", "RS_y", "LT", "RT"}
