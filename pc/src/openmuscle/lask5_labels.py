"""LASK5 finger-labeler -> ratified canonical Tier-1 labels (DATA_SCHEMA.md #0302).

The LASK5 measures finger CURL (4 pistons) plus a 2-axis thumb joystick, so it
fills the shared Tier-1 core lbl_flex_* directly and NaN-masks the Quest-only
Tier-2 lbl_ang_* (it cannot measure per-joint angles). This is the PC-side mapping
(overseer #0303: implement it on the PC too, not only phone; Tory records from
LASK5 straight on the PC).

The still-open details are PARAMETERS, not hardcoded, so the ratified STRUCTURE is
stable while phone confirms the values:
  - piston_order: which piston index is which finger.
  - calibration: per-piston (extended_raw, curled_raw) -> [0,1] via normalize_piston;
    the range is stored in capture meta so normalization is reproducible.
  - thumb: which joystick axis is flexion, normalized to [0,1] upstream.
"""

# The four fingers a LASK5 piston maps to, in wire order by default. The thumb is
# the joystick, not a piston. Phone confirms the actual piston order (#0302).
DEFAULT_PISTON_ORDER = ("index", "middle", "ring", "pinky")


def normalize_piston(raw, extended, curled):
    """Raw piston reading -> normalized flexion in [0,1] (0 = extended, 1 = curled)
    using the per-finger calibration range from capture meta. Clamped to [0,1].
    A degenerate range (extended == curled) returns 0.0."""
    span = curled - extended
    if span == 0:
        return 0.0
    return max(0.0, min(1.0, (raw - extended) / span))


def lask5_canonical(piston_flex, thumb_flex=None, piston_order=DEFAULT_PISTON_ORDER):
    """Map LASK5 finger values onto the ratified Tier-1 canonical labels.

    piston_flex: the finger pistons ALREADY normalized to [0,1] (apply
        normalize_piston with the meta calibration range upstream), in device
        order; piston_order[i] names the finger for piston_flex[i]. A None entry
        is skipped (that finger stays masked).
    thumb_flex: the thumb joystick's flexion axis normalized to [0,1], or None.

    Returns {lbl_flex_<finger>: [0,1]} for the fingers present. Tier-2 lbl_ang_*
    are NOT returned: LASK5 masks them (they read as NaN/blank in the CSV). Every
    value is clamped to [0,1] defensively."""
    def clamp01(v):
        return max(0.0, min(1.0, float(v)))

    out = {}
    for i, finger in enumerate(piston_order):
        if i < len(piston_flex) and piston_flex[i] is not None:
            out["lbl_flex_%s" % finger] = clamp01(piston_flex[i])
    if thumb_flex is not None:
        out["lbl_flex_thumb"] = clamp01(thumb_flex)
    return out
