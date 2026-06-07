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

            # Width = 1 timestamp + 60 sensor + 175 label (25 joints * 7).
            assert len(header) == 1 + 60 + 175, len(header)

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
