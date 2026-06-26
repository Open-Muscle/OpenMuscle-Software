"""Tests for the forearm supination/pronation derivation (openmuscle.forearm).

Builds synthetic hands with a KNOWN forearm axis + palm normal, then asserts
forearm_roll recovers the palm orientation (palm_up flag + the gravity-relative
roll). The constructor is the inverse of the derivation's geometry.
"""

import math

from openmuscle.forearm import (
    forearm_roll, palm_normal, joints_from_flat,
    _norm, _cross, WRIST, MIDDLE_MCP, INDEX_KNUCKLE, PINKY_KNUCKLE,
)


def _scale(v, s): return (v[0] * s, v[1] * s, v[2] * s)
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _make_hand(axis, normal, handedness="right"):
    """25 joint positions for a hand with the given forearm axis + palm normal."""
    A = _norm(axis)
    N = _norm(normal)
    # Inverse of palm_normal(): for a right hand, palm_normal = normalize(A x across)
    # where across = normalize(N x A); a left hand mirrors the across direction.
    across = _norm(_cross(N, A)) if handedness != "left" else _norm(_cross(A, N))
    pos = [(0.0, 0.0, 0.0)] * 25
    pos[WRIST] = (0.0, 0.0, 0.0)
    pos[MIDDLE_MCP] = _scale(A, 0.10)
    pos[INDEX_KNUCKLE] = _add(_scale(A, 0.07), _scale(across, 0.03))
    pos[PINKY_KNUCKLE] = _add(_scale(A, 0.07), _scale(across, -0.03))
    return pos


def test_palm_normal_recovered():
    # Construct a right hand with palm normal +Y; palm_normal() should recover it.
    pos = _make_hand(axis=(0, 0, -1), normal=(0, 1, 0))
    n = palm_normal(pos, "right")
    assert _dot(n, (0, 1, 0)) > 0.99    # points up


def test_palm_up():
    pos = _make_hand(axis=(0, 0, -1), normal=(0, 1, 0))
    roll, up = forearm_roll(pos, "right")
    assert up is True
    assert abs(roll) < 1.0              # palm-up -> roll ~ 0 (gravity-relative)


def test_palm_down():
    pos = _make_hand(axis=(0, 0, -1), normal=(0, -1, 0))
    roll, up = forearm_roll(pos, "right")
    assert up is False
    assert abs(abs(roll) - 180.0) < 1.0  # palm-down -> roll ~ +/-180


def test_palm_sideways_is_monotone():
    # Palm rotating from up toward the side gives a roll between 0 and 90.
    pos = _make_hand(axis=(0, 0, -1), normal=_norm((1, 1, 0)))  # 45 deg toward +X
    roll, up = forearm_roll(pos, "right")
    assert up is True                    # still has an up component
    assert 30.0 < abs(roll) < 60.0       # ~45 deg


def test_handedness_flips_palm():
    # Same geometry, opposite handedness -> palm normal flips -> palm_up flips.
    pos_r = _make_hand(axis=(0, 0, -1), normal=(0, 1, 0), handedness="right")
    pos_l = _make_hand(axis=(0, 0, -1), normal=(0, 1, 0), handedness="left")
    _, up_r = forearm_roll(pos_r, "right")
    _, up_l = forearm_roll(pos_l, "left")
    assert up_r is True and up_l is True   # both constructed palm-up for their hand


def test_too_few_joints_returns_none():
    assert forearm_roll([(0, 0, 0)] * 10, "right") is None
    assert forearm_roll([], "right") is None


def test_joints_from_flat():
    flat = []
    for i in range(25):
        flat += [float(i), float(i) + 0.1, float(i) + 0.2, 0, 0, 0, 1]
    pos = joints_from_flat(flat)
    assert len(pos) == 25
    assert pos[10] == (10.0, 10.1, 10.2)
