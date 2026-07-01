"""Tests for openmuscle.hand_angles: wrist-relative per-finger joint flexion
angles from Quest hand joints. The key property is invariance to translation +
rotation of the whole hand (that is why angles, not world positions, are the
right location-independent label)."""

import math

from openmuscle import hand_angles as ha

_FINGERS = {
    "thumb":  (1, 2, None, 3, 4),
    "index":  (5, 6, 7, 8, 9),
    "middle": (10, 11, 12, 13, 14),
    "ring":   (15, 16, 17, 18, 19),
    "pinky":  (20, 21, 22, 23, 24),
}
_XS = {"thumb": -0.04, "index": -0.02, "middle": 0.0, "ring": 0.02, "pinky": 0.04}


def straight_hand():
    """A fully extended hand: every finger's joints lie on a straight line along
    +z, so all flexion angles are 0."""
    P = [(0.0, 0.0, 0.0) for _ in range(25)]
    for name, joints in _FINGERS.items():
        x = _XS[name]
        for k, ji in enumerate(joints):
            if ji is None:
                continue
            P[ji] = (x, 0.0, 0.05 + 0.02 * k)
    return P


def rotate_translate(P, deg_about_y, t):
    """Rotate all points about the y axis by deg_about_y, then translate by t."""
    th = math.radians(deg_about_y)
    c, s = math.cos(th), math.sin(th)
    out = []
    for (x, y, z) in P:
        rx = x * c + z * s
        rz = -x * s + z * c
        out.append((rx + t[0], y + t[1], rz + t[2]))
    return out


def test_straight_hand_is_all_zero():
    ang = ha.per_joint_angles(straight_hand())
    assert ang is not None
    assert set(ang.keys()) == {
        "lbl_ang_index_mcp", "lbl_ang_index_pip", "lbl_ang_middle_mcp", "lbl_ang_middle_pip",
        "lbl_ang_ring_mcp", "lbl_ang_ring_pip", "lbl_ang_pinky_mcp", "lbl_ang_pinky_pip",
        "lbl_ang_thumb_mcp", "lbl_ang_thumb_ip",
    }
    for k, v in ang.items():
        assert v is not None and abs(v) < 1e-6, "%s should be ~0, got %s" % (k, v)
    flex = ha.per_finger_flexion(straight_hand())
    assert set(flex.keys()) == {"lbl_flex_thumb", "lbl_flex_index", "lbl_flex_middle",
                                "lbl_flex_ring", "lbl_flex_pinky"}
    for k, v in flex.items():
        assert abs(v) < 1e-6          # straight hand -> normalized flexion 0


def test_known_bend_at_index_pip():
    P = straight_hand()
    x = _XS["index"]
    # Bend the index PIP (joint 7) to 90 deg: send the distal + tip off in +x.
    P[8] = (x + 0.02, 0.0, 0.09)
    P[9] = (x + 0.04, 0.0, 0.09)
    ang = ha.per_joint_angles(P)
    assert abs(ang["lbl_ang_index_pip"] - 90.0) < 1e-4
    assert abs(ang["lbl_ang_index_mcp"]) < 1e-6          # MCP untouched
    assert abs(ang["lbl_ang_middle_pip"]) < 1e-6          # neighbours untouched
    # Tier-1 is NORMALIZED [0,1]: index = mean(mcp/90, pip/110) = mean(0, 90/110).
    flex = ha.per_finger_flexion(P)
    assert abs(flex["lbl_flex_index"] - (90.0 / 110.0) / 2.0) < 1e-4
    assert abs(flex["lbl_flex_middle"]) < 1e-6


def test_invariant_to_translation_and_rotation():
    P = straight_hand()
    # Curl the middle finger's MCP so there is a non-trivial angle to compare.
    xm = _XS["middle"]
    P[12] = (xm + 0.02, 0.0, 0.07)   # intermediate off in +x -> bends the MCP
    P[13] = (xm + 0.04, 0.0, 0.07)
    P[14] = (xm + 0.06, 0.0, 0.07)
    base = ha.per_joint_angles(P)
    moved = ha.per_joint_angles(rotate_translate(P, 37.0, (1.3, -2.1, 0.9)))
    for k in base:
        assert base[k] is not None and moved[k] is not None
        assert abs(base[k] - moved[k]) < 1e-6, "%s not invariant: %s vs %s" % (k, base[k], moved[k])


def test_canonical_labels_shape_and_guards():
    labels = ha.canonical_labels(straight_hand())
    assert labels is not None
    assert len(labels) == 15                          # 5 lbl_flex_ (Tier 1) + 10 lbl_ang_ (Tier 2)
    assert all(k.startswith("lbl_flex_") or k.startswith("lbl_ang_") for k in labels)
    # Too few joints -> None (not a crash).
    assert ha.per_joint_angles([(0.0, 0.0, 0.0)] * 10) is None
    assert ha.canonical_labels([(0.0, 0.0, 0.0)] * 3) is None


def test_from_flat_matches_positions():
    P = straight_hand()
    # Build a flat 25 x 7 array (px,py,pz + identity-ish rotation) from positions.
    flat = []
    for (x, y, z) in P:
        flat += [x, y, z, 0.0, 0.0, 0.0, 1.0]
    a = ha.canonical_labels_from_flat(flat)
    b = ha.canonical_labels(P)
    assert a is not None and b is not None
    for k in b:
        assert abs((a[k] or 0.0) - (b[k] or 0.0)) < 1e-9
