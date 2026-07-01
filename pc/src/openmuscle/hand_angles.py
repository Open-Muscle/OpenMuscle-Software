"""Wrist-relative per-finger joint flexion angles from Quest hand-tracking.

The capture-fix target (overseer #0297 sequence step b; ML review #0289 finding 5;
the TODO(wrist-relative-labels) in web/state.py): raw Quest joints are absolute
world POSITIONS, so a model trained on them learns where the recordings happened,
not the hand pose. Joint FLEXION ANGLES (the angle between two adjacent bone
segments) are inherently invariant to translation AND rotation of the whole hand,
so they are the location-independent label we actually want to predict.

Names + tiers match the canonical DOF schema (docs/data-schema.md, board #0292 /
#0295): Tier 1 shared core = per-finger flexion (flex_*, both VR and LASK5 fill);
Tier 2 VR-only = per-joint angles (ang_*_*). Units: degrees, flexion-positive,
0 = extended.

Joint indices follow the standard WebXR XRHand 25-joint order (the same convention
forearm.py assumes). Pending column-name ratification of the schema doc; this
module is the derivation + tests and is NOT wired to capture yet.
"""

import math

# Standard WebXR XRHand joint order (25 joints), matching forearm.py's bone table.
# Per finger: (metacarpal, proximal, intermediate, distal, tip). The thumb has no
# intermediate phalanx, so its middle slot is None.
WRIST = 0
_FINGER_JOINTS = {
    "thumb":  (1, 2, None, 3, 4),
    "index":  (5, 6, 7, 8, 9),
    "middle": (10, 11, 12, 13, 14),
    "ring":   (15, 16, 17, 18, 19),
    "pinky":  (20, 21, 22, 23, 24),
}
# Fingers that have a PIP (all but the thumb, which has an IP instead).
_LONG_FINGERS = ("index", "middle", "ring", "pinky")
_MIN_JOINTS = 25


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    m = math.sqrt(_dot(a, a))
    return (a[0] / m, a[1] / m, a[2] / m) if m > 1e-9 else None


def _flex_deg(p_from, p_via, p_to):
    """Flexion angle (degrees) at joint p_via: the angle between the incoming bone
    (p_via - p_from) and the outgoing bone (p_to - p_via). 0 when the two bones are
    collinear (straight / extended), growing as the joint curls. Invariant to any
    translation or rotation of the whole hand. None if a bone is degenerate."""
    b1 = _norm(_sub(p_via, p_from))
    b2 = _norm(_sub(p_to, p_via))
    if b1 is None or b2 is None:
        return None
    c = max(-1.0, min(1.0, _dot(b1, b2)))
    return math.degrees(math.acos(c))


def joints_from_flat(values):
    """25 (x, y, z) positions from a flat quest_hand values array (25 joints x 7
    floats per joint: px, py, pz, then rotation). Uses only the position triple."""
    n = len(values) // 7
    return [(values[i * 7], values[i * 7 + 1], values[i * 7 + 2]) for i in range(n)]


def per_joint_angles(positions):
    """Tier-2 per-joint flexion angles in DEGREES (flexion-positive, wrist-
    relative), or None if too few joints. Ratified keys (DATA_SCHEMA.md #0302,
    Quest-only): lbl_ang_<finger>_mcp / _pip, plus lbl_ang_thumb_mcp / _ip."""
    if not positions or len(positions) < _MIN_JOINTS:
        return None
    out = {}
    for name in _LONG_FINGERS:
        mc, prox, inter, dist, _tip = _FINGER_JOINTS[name]
        # MCP: metacarpal -> proximal -> intermediate. PIP: proximal -> intermediate -> distal.
        out["lbl_ang_%s_mcp" % name] = _flex_deg(positions[mc], positions[prox], positions[inter])
        out["lbl_ang_%s_pip" % name] = _flex_deg(positions[prox], positions[inter], positions[dist])
    tmc, tprox, _none, tdist, ttip = _FINGER_JOINTS["thumb"]
    # Thumb MCP: metacarpal -> proximal -> distal. Thumb IP: proximal -> distal -> tip.
    out["lbl_ang_thumb_mcp"] = _flex_deg(positions[tmc], positions[tprox], positions[tdist])
    out["lbl_ang_thumb_ip"] = _flex_deg(positions[tprox], positions[tdist], positions[ttip])
    return out


# Nominal full-flex angle per joint type (degrees) used to normalize the Tier-1
# shared core into [0,1] (0 = extended, 1 = curled) per DATA_SCHEMA.md #0302.
# Nominal, not a per-subject calibration: a rough full-curl for each joint so the
# VR-measured angles land on the same 0..1 scale LASK5 pistons already report.
# Recalibrate per subject/ROM if needed (the range would then go in capture meta).
_FULL_FLEX_DEG = {"mcp": 90.0, "pip": 110.0, "ip": 80.0}


def per_finger_flexion(positions):
    """Tier-1 SHARED CORE: per-finger flexion NORMALIZED to [0,1] (0 = extended,
    1 = curled), wrist-relative. Ratified keys (DATA_SCHEMA.md #0302): lbl_flex_
    <finger>. Each finger = mean of its joint angles, each normalized by a nominal
    full-flex and clamped to [0,1]. This is the column set BOTH VR and LASK5 fill,
    so it is what a cross-source (pooled) model trains on; None if too few joints.
    LASK5 fills these directly from its 0..1 pistons; this is the VR path."""
    a = per_joint_angles(positions)
    if a is None:
        return None

    def nrm(deg, jt):
        if deg is None:
            return None
        return max(0.0, min(1.0, deg / _FULL_FLEX_DEG[jt]))

    def finger(mcp_deg, pip_deg, second="pip"):
        vals = [v for v in (nrm(mcp_deg, "mcp"), nrm(pip_deg, second)) if v is not None]
        return sum(vals) / len(vals) if vals else None

    out = {}
    for name in _LONG_FINGERS:
        out["lbl_flex_%s" % name] = finger(a["lbl_ang_%s_mcp" % name], a["lbl_ang_%s_pip" % name])
    out["lbl_flex_thumb"] = finger(a["lbl_ang_thumb_mcp"], a["lbl_ang_thumb_ip"], second="ip")
    return out


def canonical_labels(positions):
    """All ratified canonical labels from 25 joint positions, or None: Tier-1
    lbl_flex_* (normalized [0,1]) + Tier-2 lbl_ang_* (degrees). Tier-2 is VR-only;
    a LASK5 capture NaN-masks the lbl_ang_* columns and fills only the lbl_flex_*
    core (DATA_SCHEMA.md #0302)."""
    tier2 = per_joint_angles(positions)
    if tier2 is None:
        return None
    tier1 = per_finger_flexion(positions)
    return dict(tier1, **tier2)


def canonical_labels_from_flat(values):
    """canonical_labels() straight from a flat quest_hand values array."""
    return canonical_labels(joints_from_flat(values))
