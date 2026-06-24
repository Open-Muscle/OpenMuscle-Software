"""Integration tests for quest_hand recording rectangularity.

The Quest WebXR client can momentarily lose individual joints (hand partially
out of the camera FOV). If those variable-length frames were written straight
to the CSV, rows would have different column counts -- a ragged CSV that
breaks pandas.read_csv and corrupts the capture for training.

state.py locks the label width at the first label packet (same width the
labels-schema sidecar describes) and pads/truncates every paired row to it.
These tests pin that the resulting CSV is always rectangular and consistent
with the schema, even under varying joint counts.

AppState.__init__ builds a UDPListener but does not bind a socket until
.start() is called, so we construct a real AppState pointed at a temp
captures dir and never start the listener.
"""

import csv
import json
import tempfile
import time
from pathlib import Path

from openmuscle.web.state import AppState
from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION


def _make_state(tmp):
    # udp_port is never bound (we don't call .start()); use a high port anyway.
    return AppState(udp_port=53999, captures_dir=str(tmp))


def _flexgrid_packet(device_id="fg-test", cols=15, rows=4, recv_time=None):
    matrix = [[10 + c * r for r in range(rows)] for c in range(cols)]
    return OpenMusclePacket(
        version=CURRENT_VERSION,
        device_type="flexgrid",
        device_id=device_id,
        timestamp_ms=int((recv_time or time.time()) * 1000),
        data={"matrix": matrix, "rows": rows, "cols": cols},
        receive_time=recv_time or time.time(),
    )


def _quest_payload(n_joints, handedness="right", device_id="quest-right"):
    joints = [
        {"name": f"j{i}", "pos": [i * 0.01, i * 0.02, i * 0.03],
         "rot": [0, 0, 0, 1], "valid": True}
        for i in range(n_joints)
    ]
    return {"device_id": device_id, "ts": 0, "handedness": handedness,
            "joints": joints}


def _lask5_packet(device_id="lask5-test", recv_time=None):
    return OpenMusclePacket(
        version=CURRENT_VERSION,
        device_type="lask5",
        device_id=device_id,
        timestamp_ms=int((recv_time or time.time()) * 1000),
        data={"values": [100, 200, 300, 400], "joystick": {"x": 2048, "y": 2048}},
        receive_time=recv_time or time.time(),
    )


class TestQuestRecordingRectangular:
    def test_variable_joint_counts_produce_rectangular_csv(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)

            # Register a flexgrid + a quest device so start_recording can
            # auto-pick them. The flexgrid packet sets rows/cols.
            s._handle_packet(_flexgrid_packet())
            s.ingest_quest_packet(_quest_payload(25))

            rec = s.start_recording(filename="rect.csv")
            # Quest auto-picked as label, width should lock to 25 joints * 7.
            assert rec.label_device_id == "quest-right"

            # Interleave: a full (25-joint) quest frame locks the width to 175;
            # then a partial (20-joint -> 140) frame must be padded to 175;
            # then a full frame again. Each is paired with a flexgrid frame
            # that arrives just after it (within the match window).
            for n in (25, 20, 25):
                t = time.time()
                s.ingest_quest_packet(_quest_payload(n))   # label first
                # sensor frame ~1ms later pairs with the most recent label
                s._handle_packet(_flexgrid_packet(recv_time=t + 0.001))
                time.sleep(0.005)

            result = s.stop_recording()

            csv_path = tmp / "rect.csv"
            with open(csv_path) as f:
                rows = [r for r in csv.reader(f)]

            header = rows[0]
            data_rows = rows[1:]
            assert len(data_rows) >= 1, "expected at least one paired row"

            # Every row -- header included -- has the same column count.
            widths = {len(r) for r in rows}
            assert len(widths) == 1, f"ragged CSV! column counts seen: {widths}"

            # Schema v2 width = 3 lead (ts_hub_ms, role, device_id) + 60 sensor
            # + 175 label (25 joints * 7).
            assert len(header) == 3 + 60 + 175, len(header)
            assert header[:3] == ["ts_hub_ms", "role", "device_id"], header[:3]
            # Every data row carries the lowercase role token + the sensor id.
            assert data_rows[0][1] == "left"
            assert data_rows[0][2] == "fg-test"

            # The 20-joint frame should have tripped exactly one width mismatch.
            assert result["label_width_mismatch"] == 1, result["label_width_mismatch"]

    def test_schema_sidecar_matches_csv_width(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)
            s._handle_packet(_flexgrid_packet())
            s.ingest_quest_packet(_quest_payload(25))
            s.start_recording(filename="schema.csv")

            t = time.time()
            s.ingest_quest_packet(_quest_payload(25))
            s._handle_packet(_flexgrid_packet(recv_time=t + 0.001))
            s.stop_recording()

            with open(tmp / "schema.csv") as f:
                header = next(csv.reader(f))
            with open(tmp / "schema.labels.schema.json") as f:
                schema = json.load(f)

            n_label_cols = sum(1 for h in header if h.startswith("label_"))
            assert schema["n_label_columns"] == n_label_cols == 175
            # Columns map is fully enumerated and matches the count.
            assert len(schema["columns"]) == 175


class TestRecordingDefaults:
    """Per-device-type match-window defaults + the label_source meta tag.

    Quest WebXR has higher end-to-end latency than LASK5's ESP-NOW path, so
    quest_hand recordings default to a wider 175ms match window vs LASK5's
    100ms. And every capture's meta sidecar is tagged with which label
    source produced it, so the Captures panel + downstream training can keep
    Quest-labeled and LASK5-labeled datasets separable.
    """

    def test_quest_window_defaults_to_175ms(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet())
            s.ingest_quest_packet(_quest_payload(25))
            rec = s.start_recording(filename="q.csv")   # window_ms=None
            s.stop_recording()   # close file handles before temp-dir cleanup
            assert rec.label_device_id == "quest-right"
            assert abs(rec.window_s - 0.175) < 1e-9, rec.window_s

    def test_lask5_window_defaults_to_100ms(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet())
            s._handle_packet(_lask5_packet())          # no quest device present
            rec = s.start_recording(filename="l.csv")   # window_ms=None
            s.stop_recording()
            assert rec.label_device_id == "lask5-test"
            assert abs(rec.window_s - 0.100) < 1e-9, rec.window_s

    def test_explicit_window_overrides_default(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet())
            s.ingest_quest_packet(_quest_payload(25))
            rec = s.start_recording(filename="o.csv", window_ms=250)
            s.stop_recording()
            assert abs(rec.window_s - 0.250) < 1e-9, rec.window_s

    def test_auto_pick_prefers_quest_over_lask5(self):
        # Both label sources present -> Quest wins (richer label vector),
        # per AUTO_LABEL_TYPE_PREFERENCE.
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet())
            s._handle_packet(_lask5_packet())
            s.ingest_quest_packet(_quest_payload(25))
            rec = s.start_recording(filename="p.csv")
            s.stop_recording()
            assert rec.label_device_id == "quest-right"

    def test_label_source_meta_tag_quest(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet())
            s.ingest_quest_packet(_quest_payload(25))
            s.start_recording(filename="qm.csv")
            s.stop_recording()
            meta = s.read_capture_meta("qm.csv")
            assert meta["auto"]["label_source"] == "quest_hand"
            assert meta["auto"]["window_ms"] == 175

    def test_label_source_meta_tag_lask5(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet())
            s._handle_packet(_lask5_packet())
            s.start_recording(filename="lm.csv")
            s.stop_recording()
            meta = s.read_capture_meta("lm.csv")
            assert meta["auto"]["label_source"] == "lask5"
            assert meta["auto"]["window_ms"] == 100

    def test_v2_interop_meta_keys_top_level(self):
        # Phone interop keys (board #0097) live at the TOP LEVEL, matching the
        # phone meta.json shape, not nested under auto/extras.
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet())     # device_id "fg-test"
            s._handle_packet(_lask5_packet())
            s.start_recording(filename="im.csv", role="right")
            s.stop_recording()
            meta = s.read_capture_meta("im.csv")
            assert meta["schema"] == "v2"
            assert meta["mirror"] is False
            assert meta["label_source"] == "lask5"
            assert meta["roles"] == {"fg-test": "right"}
            assert isinstance(meta["created_ms"], int)
            # Not dumped under extras.
            assert "schema" not in meta.get("extras", {})

    def test_v2_interop_label_source_quest_vocab(self):
        # PC internal device_type is quest_hand; the interop label_source uses
        # the phone wire vocabulary (quest). The raw value stays under .auto.
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet())
            s.ingest_quest_packet(_quest_payload(25))
            s.start_recording(filename="iq.csv")
            s.stop_recording()
            meta = s.read_capture_meta("iq.csv")
            assert meta["label_source"] == "quest"
            assert meta["auto"]["label_source"] == "quest_hand"

    def test_multiband_interleaved_v2_rows_and_pivot(self):
        # Two bands (left + right) + a labeler -> one interleaved v2 CSV, each
        # row tagged with its band's role/device_id; the trainer then pivots it
        # to the 120-wide Left||Right matrix. The PC half of P4 end-to-end.
        from openmuscle.data.dataset import load_training_data
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet(device_id="fg-left"))
            s._handle_packet(_flexgrid_packet(device_id="fg-right"))
            s._handle_packet(_lask5_packet(device_id="lask5-test"))
            s.start_recording(
                filename="mb.csv",
                sensor_device_id="fg-left", role="left",
                extra_sensors=[{"device_id": "fg-right", "role": "right"}],
                label_device_id="lask5-test",
            )
            for _ in range(3):
                t = time.time()
                s._handle_packet(_lask5_packet(device_id="lask5-test", recv_time=t))
                s._handle_packet(_flexgrid_packet(device_id="fg-left", recv_time=t + 0.001))
                s._handle_packet(_flexgrid_packet(device_id="fg-right", recv_time=t + 0.002))
                time.sleep(0.005)
            s.stop_recording()

            rows = [r for r in csv.reader(open(Path(d) / "mb.csv"))]
            assert rows[0][:3] == ["ts_hub_ms", "role", "device_id"]
            data = rows[1:]
            assert {r[1] for r in data} == {"left", "right"}
            assert {r[2] for r in data} == {"fg-left", "fg-right"}
            # meta roles map carries both bands
            meta = s.read_capture_meta("mb.csv")
            assert meta["roles"] == {"fg-left": "left", "fg-right": "right"}
            # The long multi-role CSV pivots to the wide 120-col matrix (15x4 = 60/side).
            X, y = load_training_data(str(Path(d) / "mb.csv"))
            assert X.shape[1] == 120
            assert X.shape[0] >= 1
