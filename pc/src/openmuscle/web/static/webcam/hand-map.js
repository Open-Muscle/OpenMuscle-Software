// OpenMuscle webcam hand labeler: geometry only (no DOM, no camera).
//
// A camera hand tracker reports 21 landmarks per hand. The OpenMuscle server
// expects a hand as 25 joints in the WebXR XRHand order (the layout the Quest
// client sends), so this module converts one into the other and expresses the
// result in a frame that does not depend on where the camera is.
//
// Kept free of browser APIs so the same file is unit-tested from Node
// (pc/tests/test_webcam_hand_map.py) against the server's own flexion math.

// The 25 joints the server and the labels-schema sidecar expect, in order.
export const JOINT_NAMES = [
    'wrist',
    'thumb-metacarpal', 'thumb-phalanx-proximal', 'thumb-phalanx-distal', 'thumb-tip',
    'index-finger-metacarpal', 'index-finger-phalanx-proximal',
    'index-finger-phalanx-intermediate', 'index-finger-phalanx-distal', 'index-finger-tip',
    'middle-finger-metacarpal', 'middle-finger-phalanx-proximal',
    'middle-finger-phalanx-intermediate', 'middle-finger-phalanx-distal', 'middle-finger-tip',
    'ring-finger-metacarpal', 'ring-finger-phalanx-proximal',
    'ring-finger-phalanx-intermediate', 'ring-finger-phalanx-distal', 'ring-finger-tip',
    'pinky-finger-metacarpal', 'pinky-finger-phalanx-proximal',
    'pinky-finger-phalanx-intermediate', 'pinky-finger-phalanx-distal', 'pinky-finger-tip',
];

// Tracker landmark indices (the common 21-point hand model).
export const LM = {
    WRIST: 0,
    THUMB_CMC: 1, THUMB_MCP: 2, THUMB_IP: 3, THUMB_TIP: 4,
    INDEX_MCP: 5, INDEX_PIP: 6, INDEX_DIP: 7, INDEX_TIP: 8,
    MIDDLE_MCP: 9, MIDDLE_PIP: 10, MIDDLE_DIP: 11, MIDDLE_TIP: 12,
    RING_MCP: 13, RING_PIP: 14, RING_DIP: 15, RING_TIP: 16,
    PINKY_MCP: 17, PINKY_PIP: 18, PINKY_DIP: 19, PINKY_TIP: 20,
};

// First landmark (the knuckle) of each long finger, in WebXR finger order.
const FINGER_MCP = [LM.INDEX_MCP, LM.MIDDLE_MCP, LM.RING_MCP, LM.PINKY_MCP];

// The tracker has no landmark for the base of a finger's metacarpal bone, which
// WebXR calls <finger>-metacarpal. We place it on the straight line from the
// wrist to the knuckle, this fraction of the way along. Knuckle flexion is the
// angle between that line and the first finger bone, so the result is the same
// wherever on the line the point sits; the fraction only keeps it off the wrist.
export const METACARPAL_FRACTION = 0.3;

export const SYNTHESIZED_JOINTS = [
    'index-finger-metacarpal', 'middle-finger-metacarpal',
    'ring-finger-metacarpal', 'pinky-finger-metacarpal',
];

// Nominal full-curl angle per joint type, degrees. Must match _FULL_FLEX_DEG in
// pc/src/openmuscle/hand_angles.py: the bars on the page show the same numbers
// the server writes to the lbl_flex_* columns.
export const FULL_FLEX_DEG = { mcp: 90.0, pip: 110.0, ip: 80.0 };

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
];
function normalize(a) {
    const m = Math.sqrt(dot(a, a));
    return m > 1e-9 ? [a[0] / m, a[1] / m, a[2] / m] : null;
}

// Tracker output is [{x, y, z}, ...]; the math here uses [x, y, z] arrays.
// Returns null unless there are 21 finite points.
export function landmarksToArrays(landmarks) {
    if (!landmarks || landmarks.length < 21) return null;
    const out = [];
    for (let i = 0; i < 21; i++) {
        const p = landmarks[i];
        const v = Array.isArray(p) ? [p[0], p[1], p[2]] : [p.x, p.y, p.z];
        if (!v.every(Number.isFinite)) return null;
        out.push(v);
    }
    return out;
}

// A frame attached to the hand, so the recorded joints describe the hand's
// pose and not where it was relative to the camera:
//   origin  the wrist
//   y       from the wrist to the middle knuckle (toward the fingers)
//   x       across the palm toward the index/thumb side, made square to y
//   z       x cross y (right-handed)
// Returns null when the landmarks are degenerate (hand edge-on or collapsed).
export function handLocalBasis(w) {
    const o = w[LM.WRIST];
    const y = normalize(sub(w[LM.MIDDLE_MCP], o));
    if (!y) return null;
    const across = sub(w[LM.INDEX_MCP], w[LM.PINKY_MCP]);
    const x = normalize(sub(across, scale(y, dot(across, y))));
    if (!x) return null;
    return { o, x, y, z: cross(x, y) };
}

function toLocal(p, b) {
    const d = sub(p, b.o);
    return [dot(d, b.x), dot(d, b.y), dot(d, b.z)];
}

// 21 tracker points (arrays, any consistent units) -> 25 WebXR-order joints in
// the hand-local frame, each {name, pos, rot, radius, valid}. Rotations are
// identity: one camera gives positions, not joint orientations. Returns null
// when the hand is degenerate, so the caller drops the frame instead of
// sending a garbage pose.
export function toWebXRJoints(w) {
    if (!w || w.length < 21) return null;
    const basis = handLocalBasis(w);
    if (!basis) return null;
    const pts = [w[LM.WRIST],
                 w[LM.THUMB_CMC], w[LM.THUMB_MCP], w[LM.THUMB_IP], w[LM.THUMB_TIP]];
    for (const mcp of FINGER_MCP) {
        const base = add(w[LM.WRIST],
                         scale(sub(w[mcp], w[LM.WRIST]), METACARPAL_FRACTION));
        pts.push(base, w[mcp], w[mcp + 1], w[mcp + 2], w[mcp + 3]);
    }
    return pts.map((p, i) => ({
        name: JOINT_NAMES[i],
        pos: toLocal(p, basis),
        rot: [0, 0, 0, 1],
        radius: 0,
        valid: true,
    }));
}

// Angle in degrees at `via` between the bone coming in (from -> via) and the
// bone going out (via -> to). 0 when straight, growing as the joint curls.
function flexDeg(from, via, to) {
    const b1 = normalize(sub(via, from));
    const b2 = normalize(sub(to, via));
    if (!b1 || !b2) return null;
    const c = Math.max(-1, Math.min(1, dot(b1, b2)));
    return Math.acos(c) * 180 / Math.PI;
}

// Per-finger flexion in [0, 1] (0 = extended, 1 = curled) from the 25 joints.
// A port of per_finger_flexion in hand_angles.py; each finger is the mean of
// its two joint angles, each divided by a nominal full curl and clamped.
export function fingerFlexion(joints) {
    if (!joints || joints.length < 25) return null;
    const P = joints.map(j => j.pos);
    const nrm = (deg, kind) => (deg == null ? null
        : Math.max(0, Math.min(1, deg / FULL_FLEX_DEG[kind])));
    const mean = (a, b) => {
        const vals = [a, b].filter(v => v != null);
        return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
    };
    const out = {
        thumb: mean(nrm(flexDeg(P[1], P[2], P[3]), 'mcp'),
                    nrm(flexDeg(P[2], P[3], P[4]), 'ip')),
    };
    ['index', 'middle', 'ring', 'pinky'].forEach((name, f) => {
        const m = 5 + f * 5;   // metacarpal, proximal, intermediate, distal, tip
        out[name] = mean(nrm(flexDeg(P[m], P[m + 1], P[m + 2]), 'mcp'),
                         nrm(flexDeg(P[m + 1], P[m + 2], P[m + 3]), 'pip'));
    });
    return out;
}
