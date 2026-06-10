"""Synthetic WebXR hand frames for hardware-free pipeline testing.

Generates the same JSON payload shape the Quest WebXR client pushes over
/ws/quest (see AppState.ingest_quest_packet), driven by a smooth latent
finger-curl signal. The companion flexgrid_matrix() derives correlated
pressure-sensor values from the SAME curls, so a capture recorded from
the simulator pair is actually learnable: train on it and the model's
predicted hand visibly tracks the real one in both viewers.

Everything here is pure (t in, data out) so tests can call it directly
without sockets or timing.
"""

import math

# Canonical WebXR hand joint order, 25 joints. Must match what the VR
# client sends and what the desktop hand viewer assumes (wrist, 4 thumb
# joints, then 5 per finger).
JOINT_NAMES = (
    "wrist",
    "thumb-metacarpal", "thumb-phalanx-proximal", "thumb-phalanx-distal",
    "thumb-tip",
    "index-finger-metacarpal", "index-finger-phalanx-proximal",
    "index-finger-phalanx-intermediate", "index-finger-phalanx-distal",
    "index-finger-tip",
    "middle-finger-metacarpal", "middle-finger-phalanx-proximal",
    "middle-finger-phalanx-intermediate", "middle-finger-phalanx-distal",
    "middle-finger-tip",
    "ring-finger-metacarpal", "ring-finger-phalanx-proximal",
    "ring-finger-phalanx-intermediate", "ring-finger-phalanx-distal",
    "ring-finger-tip",
    "pinky-finger-metacarpal", "pinky-finger-phalanx-proximal",
    "pinky-finger-phalanx-intermediate", "pinky-finger-phalanx-distal",
    "pinky-finger-tip",
)

N_JOINTS = len(JOINT_NAMES)

# Per-finger latent curl oscillators (Hz, radians). Distinct frequencies
# so no two fingers move in lockstep and a model can't cheat by learning
# one shared phase.
_CURL_FREQ = (0.13, 0.21, 0.17, 0.26, 0.31)      # thumb..pinky
_CURL_PHASE = (0.0, 1.3, 2.6, 3.9, 5.2)

# Finger geometry for a plausible right hand, meters. The hand extends
# +y from the wrist with the palm facing -z, so curling rotates segment
# directions from +y toward -z.
#   (lateral x offset of metacarpal head, metacarpal length,
#    segment lengths..., max curl angle per segment in radians)
_FINGERS = {
    "index":  (0.030, 0.085, (0.042, 0.025, 0.020), 1.35),
    "middle": (0.010, 0.090, (0.046, 0.028, 0.021), 1.40),
    "ring":   (-0.012, 0.085, (0.042, 0.026, 0.020), 1.40),
    "pinky":  (-0.032, 0.075, (0.033, 0.020, 0.017), 1.45),
}


def finger_curls(t: float) -> tuple:
    """Latent curl signal per finger at time t, each in [0, 1].

    Order: thumb, index, middle, ring, pinky.
    """
    return tuple(
        0.5 + 0.5 * math.sin(2 * math.pi * f * t + p)
        for f, p in zip(_CURL_FREQ, _CURL_PHASE)
    )


def _chain(start, direction_y, direction_z, lengths, curl, max_angle):
    """Walk a finger's phalanx chain in the y/-z plane.

    Starts at `start` heading (0, direction_y, direction_z) (unit), bends
    by curl*max_angle at each joint. Returns the list of joint positions
    after each segment.
    """
    out = []
    px, py, pz = start
    angle = math.atan2(-direction_z, direction_y)
    step = curl * max_angle / len(lengths)
    for seg in lengths:
        angle += step
        py += seg * math.cos(angle)
        pz -= seg * math.sin(angle)
        out.append((px, py, pz))
    return out


def _yaw_quat(a: float) -> list:
    """Quaternion [x,y,z,w] for a rotation of `a` radians about +y."""
    return [0.0, math.sin(a / 2), 0.0, math.cos(a / 2)]


def _yaw_rotate(p, origin, a):
    """Rotate point p about a vertical axis through `origin` by angle a."""
    x, y, z = p[0] - origin[0], p[1], p[2] - origin[2]
    ca, sa = math.cos(a), math.sin(a)
    return (origin[0] + x * ca + z * sa, y, origin[2] - x * sa + z * ca)


def hand_pose(t: float):
    """All 25 joint positions (world space, meters) plus the shared
    orientation quaternion at time t.

    Returns (positions, quat) where positions is a list of 25 (x, y, z)
    tuples in JOINT_NAMES order.
    """
    curls = finger_curls(t)

    # Wrist wanders slowly so the wrist-local canonicalization in both
    # viewers is exercised, not just fed a static origin.
    wrist = (
        0.10 * math.sin(2 * math.pi * 0.05 * t),
        1.00 + 0.05 * math.sin(2 * math.pi * 0.07 * t),
        -0.30 + 0.08 * math.cos(2 * math.pi * 0.06 * t),
    )

    positions = [wrist]

    # Thumb: 4 joints, angled out +x, curling toward the palm.
    tc = curls[0]
    base = (wrist[0] + 0.025, wrist[1] + 0.020, wrist[2])
    positions.append(base)
    px, py, pz = base
    angle = 0.5 + tc * 0.9          # radians of accumulated flexion
    for seg in (0.045, 0.032, 0.028):
        px += seg * math.sin(0.6) * (1 - tc * 0.5)   # keeps thumb fanned +x
        py += seg * math.cos(angle) * 0.8
        pz -= seg * math.sin(angle) * 0.6
        angle += tc * 0.5
        positions.append((px, py, pz))

    # Four fingers: metacarpal head, then a curling 3-segment chain plus tip.
    for i, name in enumerate(("index", "middle", "ring", "pinky")):
        dx, meta_len, segs, max_angle = _FINGERS[name]
        curl = curls[i + 1]
        meta = (wrist[0] + dx, wrist[1] + meta_len, wrist[2])
        positions.append(meta)
        chain = _chain(meta, 1.0, 0.0, segs, curl, max_angle)
        positions.extend(chain)
        # Tip: short continuation of the last segment direction.
        lx, ly, lz = chain[-1]
        sx, sy, sz = chain[-2] if len(chain) > 1 else meta
        norm = math.dist((lx, ly, lz), (sx, sy, sz)) or 1.0
        positions.append((
            lx + (lx - sx) / norm * 0.012,
            ly + (ly - sy) / norm * 0.012,
            lz + (lz - sz) / norm * 0.012,
        ))

    # Slow global yaw about the wrist; every joint reports the same
    # orientation so the viewers' inverse-wrist-rotation path gets real
    # (non-identity) input.
    yaw = 0.6 * math.sin(2 * math.pi * 0.04 * t)
    positions = [wrist] + [_yaw_rotate(p, wrist, yaw) for p in positions[1:]]
    return positions, _yaw_quat(yaw)


def hand_frame(t: float, device_id: str = "quest-sim",
               handedness: str = "right") -> dict:
    """One /ws/quest JSON payload for time t."""
    positions, quat = hand_pose(t)
    return {
        "device_id": device_id,
        "ts": int(t * 1000) % 2**31,
        "handedness": handedness,
        "joints": [
            {"name": name, "pos": [round(c, 5) for c in pos], "rot": quat}
            for name, pos in zip(JOINT_NAMES, positions)
        ],
        "meta": {"sim": True},
    }


# Flexgrid layout used by the existing simulator: 16 column-groups of 4.
_FG_COLS = 16
_FG_ROWS = 4


def flexgrid_matrix(curls, rng=None) -> list:
    """A 16x4 flexgrid matrix correlated with the given finger curls.

    Each sensor responds to a weighted blend of nearby fingers (gaussian
    falloff across the 16 columns, finger centers spread over them), plus
    optional noise from `rng` (anything with .gauss). Values are ints in
    [0, 4095] like real FlexGrid ADC counts.
    """
    centers = (1.5, 5.0, 8.0, 11.0, 14.0)   # thumb..pinky across 16 columns
    matrix = []
    for col in range(_FG_COLS):
        group = []
        for row in range(_FG_ROWS):
            signal = 0.0
            for f, center in enumerate(centers):
                w = math.exp(-((col - center) ** 2) / 6.0)
                # Rows sample the muscle at slightly different gains.
                signal += w * curls[f] * (0.7 + 0.1 * row)
            v = 400 + 3000 * signal
            if rng is not None:
                v += rng.gauss(0, 60)
            group.append(max(0, min(4095, int(v))))
        matrix.append(group)
    return matrix
