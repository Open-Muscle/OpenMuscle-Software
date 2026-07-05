"""Standard-mapping controller channels and their per-hand attribution.

A capture with a gamepad labeler stores the pad as label_0..label_20:
axes 0..3 (label_0..3) then buttons 0..16 (label_4..20), per the W3C Gamepad
"standard" mapping. When both bracelets record against one controller, each
forearm can only physically drive its OWN hand's controls: the left forearm
moves the left stick, LB, LT, the D-pad, and L3; the right forearm moves the
right stick, A/B/X/Y, RB, RT, and R3. Training a per-hand model on the full
21-channel vector forces each forearm to also predict the other hand's controls,
for which it has no signal, dragging the score down. This module maps each label
column to the hand that produces it so training and analysis can keep only the
physiologically-driven channels per band.

The Guide button (label_20) sits dead-center and is driven by neither forearm in
normal play; it is tagged 'center' and dropped from both per-hand sets.
"""

# label index -> (name, hand, kind). label index = axis index for axes (0..3),
# and 4 + button index for buttons (buttons 0..16 -> label 4..20).
CHANNELS = [
    {"i": 0,  "name": "LS_x",      "hand": "left",   "kind": "stick"},
    {"i": 1,  "name": "LS_y",      "hand": "left",   "kind": "stick"},
    {"i": 2,  "name": "RS_x",      "hand": "right",  "kind": "stick"},
    {"i": 3,  "name": "RS_y",      "hand": "right",  "kind": "stick"},
    {"i": 4,  "name": "A",         "hand": "right",  "kind": "face"},
    {"i": 5,  "name": "B",         "hand": "right",  "kind": "face"},
    {"i": 6,  "name": "X",         "hand": "right",  "kind": "face"},
    {"i": 7,  "name": "Y",         "hand": "right",  "kind": "face"},
    {"i": 8,  "name": "LB",        "hand": "left",   "kind": "bumper"},
    {"i": 9,  "name": "RB",        "hand": "right",  "kind": "bumper"},
    {"i": 10, "name": "LT",        "hand": "left",   "kind": "trigger"},
    {"i": 11, "name": "RT",        "hand": "right",  "kind": "trigger"},
    {"i": 12, "name": "Back",      "hand": "left",   "kind": "system"},
    {"i": 13, "name": "Start",     "hand": "right",  "kind": "system"},
    {"i": 14, "name": "L3",        "hand": "left",   "kind": "stick_click"},
    {"i": 15, "name": "R3",        "hand": "right",  "kind": "stick_click"},
    {"i": 16, "name": "DpadUp",    "hand": "left",   "kind": "dpad"},
    {"i": 17, "name": "DpadDown",  "hand": "left",   "kind": "dpad"},
    {"i": 18, "name": "DpadLeft",  "hand": "left",   "kind": "dpad"},
    {"i": 19, "name": "DpadRight", "hand": "left",   "kind": "dpad"},
    {"i": 20, "name": "Guide",     "hand": "center", "kind": "system"},
]

# Continuous controls (stick axes + analog triggers). These are the natural
# regression targets; the rest are digital 0/1 events.
ANALOG_KINDS = {"stick", "trigger"}


def channels_for_hand(hand: str):
    """The CHANNELS entries a given forearm physically drives ('left'|'right')."""
    return [c for c in CHANNELS if c["hand"] == hand]


def label_indices_for_hand(hand: str):
    """label_* column indices for a hand, e.g. left -> [0,1,8,10,12,14,16,17,18,19]."""
    return [c["i"] for c in channels_for_hand(hand)]


def label_names_for_hand(hand: str):
    return [c["name"] for c in channels_for_hand(hand)]


def is_analog(channel: dict) -> bool:
    return channel["kind"] in ANALOG_KINDS
