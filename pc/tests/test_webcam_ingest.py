"""Webcam hand labeler: server-side ingest and recording.

The /webcam page sends hand joints in the same 25-joint layout as the Quest
client, as device_type "webcam_hand". These tests pin what makes it a distinct,
honestly-labelled source rather than a Quest look-alike:

  - it registers as webcam_hand and is recorded with the canonical lbl_*
    columns but WITHOUT the forearm columns (its joints are in a hand-local
    frame, so there is no gravity direction to measure roll against);
  - the capture says where its labels came from (labels-schema sidecar and
    both label_source fields);
  - a headset or a LASK5 that is present still wins the auto-pick;
  - the Quest path is unchanged.

Plus the mirrored-session flag, which rides on the same change: captures made
in a session started as mirrored are tagged mirror:true (PROTOCOL.md 8.5).
"""

import csv
import json
import math
import tempfile
import time
from pathlib import Path

from openmuscle.protocol.schema import CURRENT_VERSION, OpenMusclePacket
from openmuscle.simulate.quest_hand import JOINT_NAMES
from openmuscle.web.state import HAND_POSE_TYPES, AppState


def _make_state(tmp):
    return AppState(udp_port=53998, captures_dir=str(tmp))


def _flexgrid_packet(device_id="fg-test", cols=15, rows=4, recv_time=None):
    matrix = [[10 + c * r for r in range(rows)] for c in range(cols)]
    return OpenMusclePacket(
        version=CURRENT_VERSION, device_type="flexgrid", device_id=device_id,
        timestamp_ms=int((recv_time or time.time()) * 1000),
        data={"matrix": matrix, "rows": rows, "cols": cols},
        receive_time=recv_time or time.time(),
    )


def _lask5_packet(device_id="lask5-test"):
    return OpenMusclePacket(
        version=CURRENT_VERSION, device_type="lask5", device_id=device_id,
        timestamp_ms=int(time.time() * 1000),
        data={"values": [0.1, 0.2, 0.3, 0.4], "joystick": {"x": 2048, "y": 2048}},
        receive_time=time.time(),
    )


def _hand_joints(mcp_deg=45.0, pip_deg=55.0):
    """25 WebXR-order joints for a hand whose four long fingers are all bent
    by mcp_deg at the knuckle and pip_deg at the middle joint. The hand lies in
    the x/y plane: fingers run along +y and curl toward +z."""
    wrist = [0.0, 0.0, 0.0]
    pts = [wrist,
           [0.02, 0.02, 0.0], [0.04, 0.04, 0.0], [0.06, 0.06, 0.0], [0.08, 0.08, 0.0]]
    for x in (0.03, 0.01, -0.01, -0.03):
        meta = [x * 0.3, 0.03, 0.0]
        knuckle = [x, 0.09, 0.0]
        # The metacarpal-to-knuckle bone is not exactly along +y (it fans out in
        # x), so measure the bend from that bone's own direction.
        base = [knuckle[i] - meta[i] for i in range(3)]
        norm = math.sqrt(sum(v * v for v in base))
        u = [v / norm for v in base]

        def bend(p, total_deg, length, u=u):
            a = math.radians(total_deg)
            return [p[0] + length * u[0] * math.cos(a),
                    p[1] + length * u[1] * math.cos(a),
                    p[2] + length * math.sin(a)]

        pip = bend(knuckle, mcp_deg, 0.04)
        dip = bend(pip, mcp_deg + pip_deg, 0.025)
        tip = bend(dip, mcp_deg + pip_deg, 0.02)
        pts.extend([meta, knuckle, pip, dip, tip])
    assert len(pts) == 25
    return [{"name": JOINT_NAMES[i], "pos": p, "rot": [0, 0, 0, 1],
             "radius": 0, "valid": True} for i, p in enumerate(pts)]


def _webcam_payload(handedness="right", **kw):
    return {
        "device_id": f"webcam-{handedness}", "ts": 0, "handedness": handedness,
        "joints": _hand_joints(**kw),
        "meta": {"tracker": "mediapipe-hand-landmarker", "frame": "hand_local"},
    }


def _record(s, name, n=3, payload=None):
    rec = s.start_recording(filename=name)
    for _ in range(n):
        t = time.time()
        s.ingest_webcam_packet(payload or _webcam_payload())
        s._handle_packet(_flexgrid_packet(recv_time=t + 0.001))
        time.sleep(0.005)
    s.stop_recording()
    return rec


class TestWebcamIngest:
    def test_registers_as_webcam_hand(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s.ingest_webcam_packet(_webcam_payload("left"))
            dev = s.devices["webcam-left"]
            assert dev.device_type == "webcam_hand"
            assert "webcam_hand" in HAND_POSE_TYPES

    def test_empty_frame_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s.ingest_webcam_packet({"device_id": "webcam-right", "joints": []})
            assert "webcam-right" not in s.devices

    def test_quest_ingest_still_quest(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            payload = _webcam_payload("right")
            payload["device_id"] = "quest-right"
            s.ingest_quest_packet(payload)
            assert s.devices["quest-right"].device_type == "quest_hand"


class TestWebcamRecording:
    def test_columns_canonical_without_forearm(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)
            s._handle_packet(_flexgrid_packet())
            s.ingest_webcam_packet(_webcam_payload())
            rec = _record(s, "cam.csv")
            assert rec.label_device_id == "webcam-right"

            with open(tmp / "cam.csv") as f:
                rows = list(csv.reader(f))
            header, data = rows[0], rows[1:]
            assert data, "expected at least one paired row"
            assert len({len(r) for r in rows}) == 1, "ragged CSV"
            # 3 lead + 60 sensor + 175 label (25 joints * 7) + 12 IMU + 15
            # canonical. No forearm_roll_deg / palm_up: not gravity-referenced.
            assert len(header) == 3 + 60 + 175 + 12 + 15, len(header)
            assert "forearm_roll_deg" not in header and "palm_up" not in header
            assert header[-15:-10] == [
                "lbl_flex_thumb", "lbl_flex_index", "lbl_flex_middle",
                "lbl_flex_ring", "lbl_flex_pinky"]

    def test_canonical_values_match_the_pose(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)
            s._handle_packet(_flexgrid_packet())
            payload = _webcam_payload(mcp_deg=45.0, pip_deg=55.0)
            s.ingest_webcam_packet(payload)
            _record(s, "pose.csv", payload=payload)
            with open(tmp / "pose.csv") as f:
                row = next(csv.DictReader(f))
            # 45 deg of a nominal 90 at the knuckle and 55 of 110 at the middle
            # joint are both half a full curl.
            for finger in ("index", "middle", "ring", "pinky"):
                assert abs(float(row[f"lbl_ang_{finger}_mcp"]) - 45.0) < 0.5
                assert abs(float(row[f"lbl_ang_{finger}_pip"]) - 55.0) < 0.5
                assert abs(float(row[f"lbl_flex_{finger}"]) - 0.5) < 0.01

    def test_capture_records_its_label_source(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)
            s._handle_packet(_flexgrid_packet())
            s.ingest_webcam_packet(_webcam_payload())
            _record(s, "src.csv")

            schema = json.loads((tmp / "src.labels.schema.json").read_text())
            assert schema["label_source"] == "webcam_hand"
            assert schema["n_joints"] == 25 and schema["n_label_columns"] == 175
            assert schema["source_detail"]["frame"] == "hand_local"

            meta = s.read_capture_meta("src.csv")
            assert meta["label_source"] == "webcam"
            assert meta["auto"]["label_source"] == "webcam_hand"
            assert meta["auto"]["canonical_columns"] is True
            assert meta["auto"]["forearm_columns"] is False

    def test_quest_schema_source_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)
            s._handle_packet(_flexgrid_packet())
            payload = _webcam_payload()
            payload["device_id"] = "quest-right"
            payload.pop("meta")
            s.ingest_quest_packet(payload)
            s.start_recording(filename="q.csv")
            t = time.time()
            s.ingest_quest_packet(payload)
            s._handle_packet(_flexgrid_packet(recv_time=t + 0.001))
            s.stop_recording()
            schema = json.loads((tmp / "q.labels.schema.json").read_text())
            assert schema["label_source"] == "quest_hand"
            assert "source_detail" not in schema
            assert s.read_capture_meta("q.csv")["label_source"] == "quest"


class TestAutoPickOrder:
    def test_lask5_beats_webcam(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s.ingest_webcam_packet(_webcam_payload())
            s._handle_packet(_lask5_packet())
            assert s._auto_pick_label() == "lask5-test"

    def test_quest_beats_webcam(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s.ingest_webcam_packet(_webcam_payload())
            q = _webcam_payload()
            q["device_id"] = "quest-right"
            s.ingest_quest_packet(q)
            assert s._auto_pick_label() == "quest-right"

    def test_webcam_alone_is_picked(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s.ingest_webcam_packet(_webcam_payload("left"))
            assert s._auto_pick_label() == "webcam-left"


class TestMirroredSession:
    def _capture_in_session(self, tmp, **session_kw):
        s = _make_state(tmp)
        s._handle_packet(_flexgrid_packet())
        s.ingest_webcam_packet(_webcam_payload())
        session = s.start_session(name="t", **session_kw)
        _record(s, "m.csv")
        return s, session

    def test_mirrored_session_tags_capture(self):
        with tempfile.TemporaryDirectory() as d:
            s, session = self._capture_in_session(
                Path(d), labeler_source="webcam", mirrored=True, labeled_hand="right")
            assert session["mirrored"] is True
            assert session["labeled_hand"] == "right"
            assert session["labeler_source"] == "webcam"
            meta = s.read_capture_meta("m.csv")
            assert meta["mirror"] is True
            assert meta["auto"]["session_mirrored"] is True
            assert meta["auto"]["session_labeled_hand"] == "right"

    def test_ordinary_session_is_not_mirrored(self):
        with tempfile.TemporaryDirectory() as d:
            s, session = self._capture_in_session(Path(d), labeler_source="webcam")
            assert session["mirrored"] is False
            assert session["labeled_hand"] is None
            assert s.read_capture_meta("m.csv")["mirror"] is False

    def test_no_session_is_not_mirrored(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet())
            s.ingest_webcam_packet(_webcam_payload())
            _record(s, "n.csv")
            assert s.read_capture_meta("n.csv")["mirror"] is False

    def test_bad_labeled_hand_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            session = s.start_session(name="t", mirrored=True, labeled_hand="both")
            assert session["mirrored"] is True
            assert session["labeled_hand"] is None
