"""Forearm supination/pronation from Quest hand-tracking joint POSITIONS.

Derivation engine for the schema-v2 forearm-orientation columns (board #0207):
a continuous forearm-roll angle + a palm-up/down flag, as VR ground-truth for the
muscle -> forearm-rotation model.

Position-based on purpose: it uses the wrist + knuckle joint positions (which are
unambiguous world coordinates) rather than the wrist quaternion, so it does not
depend on the WebXR XRHand joint-frame convention. Handedness flips the palm-normal
sign (a right hand's cross product points out the back of the hand; a left hand
mirrors it).

REFERENCE FRAME NOTE (surfaced for #0207 ratification): a TRUE anatomical roll
(0 = forearm neutral) needs the elbow/forearm direction, which Quest hand tracking
does NOT provide. What IS computable from the hand alone is the palm orientation
relative to GRAVITY. So `forearm_roll` here is GRAVITY-RELATIVE: 0 = palm-up (palm
normal aligned with world up), +/-180 = palm-down, +/-90 = palm vertical. The
ratified spec's zero/sign is then a fixed offset applied on top (one constant).

NOT wired to capture/disk - the column write waits for spec ratification (overseer
#0201: do not write to disk until ratified). This module is the derivation + tests.
"""

import math

# Canonical WebXR joint indices (cf. hand-viewer bone table): wrist=0; finger
# metacarpals 5/10/15/20; knuckles (proximal/MCP) 6/11/16/21.
WRIST = 0
MIDDLE_MCP = 10        # wrist -> middle metacarpal = the hand's long axis
INDEX_KNUCKLE = 6      # index proximal (MCP knuckle), thumb side
PINKY_KNUCKLE = 21     # pinky proximal (MCP knuckle), little-finger side
_MIN_JOINTS = 22       # need up to index 21


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])
def _norm(a):
    m = math.sqrt(_dot(a, a))
    return (a[0] / m, a[1] / m, a[2] / m) if m > 1e-9 else (0.0, 0.0, 0.0)


def joints_from_flat(values):
    """25 (x,y,z) positions from a flat quest_hand values array (25 joints x 7
    floats: px,py,pz, rx,ry,rz,rw)."""
    n = len(values) // 7
    return [(values[i * 7], values[i * 7 + 1], values[i * 7 + 2]) for i in range(n)]


def _signed_angle_about_axis(v_from, v_to, axis):
    """Signed angle (radians) from v_from to v_to about `axis` (right-hand rule),
    both projected onto the plane perpendicular to axis."""
    f = _sub(v_from, tuple(axis[i] * _dot(v_from, axis) for i in range(3)))
    t = _sub(v_to, tuple(axis[i] * _dot(v_to, axis) for i in range(3)))
    f = _norm(f)
    t = _norm(t)
    cos = _dot(f, t)
    sin = _dot(axis, _cross(f, t))
    return math.atan2(sin, cos)


def palm_normal(positions, handedness="right"):
    """Unit palm normal (points OUT of the palm) from the joint positions."""
    wrist = positions[WRIST]
    v_index = _sub(positions[INDEX_KNUCKLE], wrist)
    v_pinky = _sub(positions[PINKY_KNUCKLE], wrist)
    n = _cross(v_index, v_pinky)
    # Right hand: index->pinky sweep makes cross() point out the BACK of the
    # hand; negate so it points out the PALM. Left hand mirrors.
    if handedness != "left":
        n = (-n[0], -n[1], -n[2])
    return _norm(n)


def forearm_roll(positions, handedness="right", gravity_up=(0.0, 1.0, 0.0)):
    """(forearm_roll_deg, palm_up) from hand joint positions, or None if too few.

    forearm_roll_deg: gravity-relative roll in [-180, 180]. 0 = palm-up (palm
        normal aligned with world up); magnitude grows toward palm-down (+/-180).
        The ratified anatomical zero/sign (#0207) is a fixed offset on this.
    palm_up: bool, the palm faces up (palm normal has a positive up component).
    """
    if not positions or len(positions) < _MIN_JOINTS:
        return None
    axis = _norm(_sub(positions[MIDDLE_MCP], positions[WRIST]))  # hand long axis
    if axis == (0.0, 0.0, 0.0):
        return None
    pn = palm_normal(positions, handedness)
    roll_rad = _signed_angle_about_axis(gravity_up, pn, axis)
    palm_up = _dot(pn, gravity_up) > 0.0
    return (math.degrees(roll_rad), palm_up)
