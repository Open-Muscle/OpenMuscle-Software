"""Cross-check the webcam page's JavaScript against the server's Python.

web/static/webcam/hand-map.js converts a camera tracker's 21 hand landmarks
into the 25 WebXR-order joints the server records, and computes the finger-curl
numbers the page shows. Both halves have a Python counterpart that must agree:

  - the joint order has to be the one the recorder and labels-schema assume
    (simulate.quest_hand.JOINT_NAMES), or every label column is mislabelled;
  - the curl numbers on the page have to equal what hand_angles writes to the
    lbl_flex_* columns, or the operator is shown one thing and records another.

So this test runs the real JS file under Node on a synthetic hand with known
joint angles and compares. It also pins the property the whole design rests
on: moving, turning or mirroring the hand in front of the camera does not
change the recorded finger angles. Skipped when Node is not installed.
"""

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from openmuscle import hand_angles
from openmuscle.simulate.quest_hand import JOINT_NAMES

HAND_MAP_JS = (Path(__file__).resolve().parents[1]
               / "src" / "openmuscle" / "web" / "static" / "webcam" / "hand-map.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="Node.js not installed")

# Loads the module through a data: URL so it works whatever Node decides about
# a bare .js file, then prints everything the Python side wants to check.
RUNNER = """
import { readFileSync } from 'node:fs';
const src = readFileSync(process.argv[2], 'utf8');
const mod = await import('data:text/javascript;base64,' + Buffer.from(src).toString('base64'));
const hands = JSON.parse(readFileSync(process.argv[3], 'utf8'));
const out = hands.map(lm => {
    const joints = mod.toWebXRJoints(mod.landmarksToArrays(lm));
    return joints ? { joints, flexion: mod.fingerFlexion(joints) } : null;
});
console.log(JSON.stringify({
    names: mod.JOINT_NAMES, full: mod.FULL_FLEX_DEG,
    synthesized: mod.SYNTHESIZED_JOINTS, out,
}));
"""

FINGERS = ("index", "middle", "ring", "pinky")
# Known pose: (knuckle bend, middle-joint bend) per long finger, degrees.
POSE = {"index": (20.0, 30.0), "middle": (45.0, 55.0),
        "ring": (70.0, 90.0), "pinky": (10.0, 100.0)}
THUMB = (25.0, 40.0)   # thumb knuckle bend, thumb tip-joint bend


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v]


def _tracker_hand():
    """21 landmarks in the tracker's order for a hand with the POSE above.
    Flat palm in the x/y plane, fingers along +y, curling toward +z."""
    wrist = [0.0, 0.0, 0.0]

    def chain(start, u, lengths, bends):
        """Points along a finger: each bone turns `bends[i]` further toward +z
        from the starting direction u (a unit vector with no z part)."""
        pts, p, total = [], start, 0.0
        for length, bend in zip(lengths, bends):
            total += bend
            a = math.radians(total)
            p = [p[0] + length * u[0] * math.cos(a),
                 p[1] + length * u[1] * math.cos(a),
                 p[2] + length * math.sin(a)]
            pts.append(p)
        return pts

    cmc = [0.03, 0.02, 0.0]
    u_t = _unit([0.6, 0.8, 0.0])
    t_mcp = [cmc[i] + 0.04 * u_t[i] for i in range(3)]
    t_ip, t_tip = chain(t_mcp, u_t, (0.03, 0.025), THUMB)
    pts = [wrist, cmc, t_mcp, t_ip, t_tip]
    for finger, x in zip(FINGERS, (0.03, 0.01, -0.01, -0.03)):
        knuckle = [x, 0.09, 0.0]
        u = _unit(knuckle)   # the page puts the metacarpal on the wrist-knuckle line
        mcp, pip = POSE[finger]
        pts.append(knuckle)
        pts.extend(chain(knuckle, u, (0.04, 0.025, 0.02), (mcp, pip, 0.0)))
    assert len(pts) == 21
    return pts


def _moved(pts, mirror=False):
    """The same hand somewhere else: turned about two axes, shifted, and
    optionally reflected (a camera sees a mirror image of the other hand)."""
    ax, az = math.radians(37.0), math.radians(-115.0)
    out = []
    for x, y, z in pts:
        if mirror:
            x = -x
        y, z = y * math.cos(ax) - z * math.sin(ax), y * math.sin(ax) + z * math.cos(ax)
        x, y = x * math.cos(az) - y * math.sin(az), x * math.sin(az) + y * math.cos(az)
        out.append([x + 0.4, y - 0.25, z + 1.3])
    return out


@pytest.fixture(scope="module")
def js():
    base = _tracker_hand()
    hands = [base, _moved(base), _moved(base, mirror=True),
             [[0.0, 0.0, 0.0]] * 21]          # collapsed hand: must be rejected
    with tempfile.TemporaryDirectory() as d:
        runner = Path(d) / "run.mjs"
        runner.write_text(RUNNER)
        data = Path(d) / "hands.json"
        data.write_text(json.dumps(
            [[{"x": p[0], "y": p[1], "z": p[2]} for p in h] for h in hands]))
        proc = subprocess.run(["node", str(runner), str(HAND_MAP_JS), str(data)],
                              capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_joint_order_matches_the_recorder(js):
    assert tuple(js["names"]) == JOINT_NAMES
    assert all(j["name"] == n for j, n in zip(js["out"][0]["joints"], JOINT_NAMES))
    assert set(js["synthesized"]) <= set(JOINT_NAMES)


def test_full_flex_constants_match_python(js):
    assert js["full"] == hand_angles._FULL_FLEX_DEG


def test_known_pose_gives_known_angles(js):
    positions = [tuple(j["pos"]) for j in js["out"][0]["joints"]]
    angles = hand_angles.per_joint_angles(positions)
    for finger, (mcp, pip) in POSE.items():
        assert angles[f"lbl_ang_{finger}_mcp"] == pytest.approx(mcp, abs=1e-6)
        assert angles[f"lbl_ang_{finger}_pip"] == pytest.approx(pip, abs=1e-6)
    assert angles["lbl_ang_thumb_mcp"] == pytest.approx(THUMB[0], abs=1e-6)
    assert angles["lbl_ang_thumb_ip"] == pytest.approx(THUMB[1], abs=1e-6)


def test_page_bars_equal_recorded_labels(js):
    for hand in js["out"][:3]:
        positions = [tuple(j["pos"]) for j in hand["joints"]]
        recorded = hand_angles.per_finger_flexion(positions)
        for finger in ("thumb",) + FINGERS:
            assert hand["flexion"][finger] == pytest.approx(
                recorded[f"lbl_flex_{finger}"], abs=1e-9)


def test_pose_is_independent_of_where_the_hand_is(js):
    base, moved, mirrored = js["out"][:3]
    for finger in ("thumb",) + FINGERS:
        assert moved["flexion"][finger] == pytest.approx(base["flexion"][finger], abs=1e-9)
        assert mirrored["flexion"][finger] == pytest.approx(base["flexion"][finger], abs=1e-9)
    # Turning and shifting the hand leaves the hand-local joints unchanged.
    for a, b in zip(base["joints"], moved["joints"]):
        assert a["pos"] == pytest.approx(b["pos"], abs=1e-9)


def test_hand_local_frame(js):
    joints = {j["name"]: j["pos"] for j in js["out"][0]["joints"]}
    assert joints["wrist"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-12)
    mx, my, mz = joints["middle-finger-phalanx-proximal"]
    assert my > 0 and mx == pytest.approx(0.0, abs=1e-9) and mz == pytest.approx(0.0, abs=1e-9)
    assert (joints["index-finger-phalanx-proximal"][0]
            > joints["pinky-finger-phalanx-proximal"][0])
    assert all(j["rot"] == [0, 0, 0, 1] for j in js["out"][0]["joints"])


def test_collapsed_hand_is_rejected(js):
    assert js["out"][3] is None
