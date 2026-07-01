"""Tests for openmuscle.lask5_labels: LASK5 pistons + thumb joystick -> the
ratified Tier-1 canonical lbl_flex_* (DATA_SCHEMA.md #0302), with the open details
(piston order, calibration, thumb) as parameters."""

from openmuscle import lask5_labels as ll


def test_maps_four_pistons_plus_thumb():
    out = ll.lask5_canonical([0.1, 0.5, 0.9, 1.0], thumb_flex=0.3)
    assert out == {
        "lbl_flex_index": 0.1, "lbl_flex_middle": 0.5,
        "lbl_flex_ring": 0.9, "lbl_flex_pinky": 1.0, "lbl_flex_thumb": 0.3,
    }


def test_tier2_is_masked():
    # LASK5 cannot measure per-joint angles -> no lbl_ang_* (they read NaN in CSV).
    out = ll.lask5_canonical([0.2, 0.2, 0.2, 0.2], thumb_flex=0.2)
    assert not any(k.startswith("lbl_ang_") for k in out)


def test_custom_piston_order():
    out = ll.lask5_canonical([0.1, 0.2, 0.3, 0.4],
                             piston_order=("pinky", "ring", "middle", "index"))
    assert out["lbl_flex_pinky"] == 0.1
    assert out["lbl_flex_index"] == 0.4


def test_thumb_optional_and_none_pistons_skipped():
    out = ll.lask5_canonical([0.1, None, 0.3, 0.4])
    assert "lbl_flex_thumb" not in out          # no joystick -> no thumb
    assert "lbl_flex_middle" not in out          # None piston stays masked
    assert out["lbl_flex_index"] == 0.1


def test_values_clamped_to_unit_range():
    out = ll.lask5_canonical([-0.5, 1.5, 0.5, 0.5], thumb_flex=2.0)
    assert out["lbl_flex_index"] == 0.0
    assert out["lbl_flex_middle"] == 1.0
    assert out["lbl_flex_thumb"] == 1.0


def test_normalize_piston_range_and_clamp():
    assert ll.normalize_piston(0, 0, 100) == 0.0
    assert ll.normalize_piston(100, 0, 100) == 1.0
    assert ll.normalize_piston(50, 0, 100) == 0.5
    assert ll.normalize_piston(-10, 0, 100) == 0.0     # clamp low
    assert ll.normalize_piston(150, 0, 100) == 1.0     # clamp high
    assert ll.normalize_piston(50, 100, 100) == 0.0    # degenerate range
