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
            # + 175 label (25 joints * 7) + 12 IMU (imu_* band + lbl_imu_*
            # labeler; with_imu defaults on). Rectangularity must still hold.
            assert len(header) == 3 + 60 + 175 + 12, len(header)
            assert header[:3] == ["ts_hub_ms", "role", "device_id"], header[:3]
            assert "imu_gx" in header and "lbl_imu_gx" in header
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

    def test_bilateral_per_side_labeling(self):
        # Two hands: the LEFT band is matched to the left-hand labeler and the
        # RIGHT band to the right-hand labeler (side-aware), so each band's rows
        # carry its OWN hand's label. Markers: left-hand joints pos=2.0,
        # right-hand pos=100.0, so the recorded label sets are distinguishable.
        def _hand(handedness, device_id, marker):
            joints = [{"name": f"j{i}", "pos": [marker, marker, marker],
                       "rot": [0, 0, 0, 1]} for i in range(25)]
            return {"device_id": device_id, "handedness": handedness,
                    "ts": 0, "joints": joints}

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)
            s._handle_packet(_flexgrid_packet(device_id="fg-left"))
            s._handle_packet(_flexgrid_packet(device_id="fg-right"))
            s.ingest_quest_packet(_hand("left", "quest-left", 2.0))
            s.ingest_quest_packet(_hand("right", "quest-right", 100.0))
            rec = s.start_recording(
                filename="bi.csv", sensor_device_id="fg-left", role="left",
                extra_sensors=[{"device_id": "fg-right", "role": "right"}],
                side_labelers={"left": "quest-left", "right": "quest-right"})
            assert set(rec.side_matchers) == {"left", "right"}
            assert rec.label_id_side == {"quest-left": "left", "quest-right": "right"}

            for _ in range(3):
                t = time.time()
                s.ingest_quest_packet(_hand("left", "quest-left", 2.0))
                s.ingest_quest_packet(_hand("right", "quest-right", 100.0))
                s._handle_packet(_flexgrid_packet(device_id="fg-left", recv_time=t + 0.001))
                s._handle_packet(_flexgrid_packet(device_id="fg-right", recv_time=t + 0.001))
                time.sleep(0.005)
            s.stop_recording()

            rows = list(csv.reader(open(tmp / "bi.csv")))
            header, data = rows[0], rows[1:]
            role_i = header.index("role")
            lbl_i = [i for i, c in enumerate(header) if c.startswith("label_")]
            left_rows = [r for r in data if r[role_i] == "left"]
            right_rows = [r for r in data if r[role_i] == "right"]
            assert left_rows and right_rows
            for r in left_rows:        # left band paired to left hand (2.0)
                vals = {r[i] for i in lbl_i}
                assert "2.0" in vals and "100.0" not in vals
            for r in right_rows:       # right band paired to right hand (100.0)
                vals = {r[i] for i in lbl_i}
                assert "100.0" in vals and "2.0" not in vals

    def test_bilateral_requires_both_sides(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s._handle_packet(_flexgrid_packet(device_id="fg-left"))
            s.ingest_quest_packet({"device_id": "quest-left", "handedness": "left",
                                   "joints": [{"name": "w", "pos": [1, 1, 1],
                                               "rot": [0, 0, 0, 1]}]})
            with __import__("pytest").raises(RuntimeError):
                s.start_recording(sensor_device_id="fg-left",
                                  side_labelers={"left": "quest-left"})  # no right

    def _bilateral_setup(self, s, d):
        s.discovery.auto_subscribe = False
        s.discovery.cache_path = str(Path(d) / "disc.json")
        s._handle_packet(_flexgrid_packet(device_id="fg-left"))
        s._handle_packet(_flexgrid_packet(device_id="fg-right"))
        for did in ("fg-left", "fg-right"):
            s.discovery.on_announce(
                {"v": "1.0", "type": "announce", "id": did, "role": "source",
                 "dev": "flexgrid", "caps": [], "matrix": [15, 4],
                 "services": {"cmd": 8001}}, "127.0.0.1")
        s.discovery.set_role("fg-left", "left")
        s.discovery.set_role("fg-right", "right")

    def test_start_bilateral_from_role_tags(self):
        # The two-hand FLOW: tag bands left/right, both Quest hands stream as
        # quest-left/quest-right (?arm=both), start_bilateral wires per-side.
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            self._bilateral_setup(s, d)
            s.ingest_quest_packet({"device_id": "quest-left", "handedness": "left",
                                   "joints": [{"name": "w", "pos": [1, 1, 1],
                                               "rot": [0, 0, 0, 1]}]})
            s.ingest_quest_packet({"device_id": "quest-right", "handedness": "right",
                                   "joints": [{"name": "w", "pos": [2, 2, 2],
                                               "rot": [0, 0, 0, 1]}]})
            rec = s.start_bilateral_recording(filename="bi2.csv")
            assert rec.sensors == {"fg-left": "left", "fg-right": "right"}
            assert rec.label_id_side == {"quest-left": "left", "quest-right": "right"}
            s.stop_recording()

    def test_start_bilateral_requires_both_quest_streams(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            self._bilateral_setup(s, d)
            s.ingest_quest_packet({"device_id": "quest-left", "handedness": "left",
                                   "joints": [{"name": "w", "pos": [1, 1, 1],
                                               "rot": [0, 0, 0, 1]}]})
            with __import__("pytest").raises(RuntimeError):   # quest-right missing
                s.start_bilateral_recording(filename="bad.csv")

    def test_start_multiband_from_role_tags(self):
        # The record FLOW: tag bands + labeler in discovery (Sources-panel
        # role-UX), then start_multiband_recording gathers them automatically.
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s.discovery.auto_subscribe = False               # no TCP attempts
            s.discovery.cache_path = str(Path(d) / "disc.json")  # isolate the cache
            s._handle_packet(_flexgrid_packet(device_id="fg-left"))
            s._handle_packet(_flexgrid_packet(device_id="fg-right"))
            s._handle_packet(_lask5_packet(device_id="lask5-test"))
            for did, dev in [("fg-left", "flexgrid"), ("fg-right", "flexgrid"),
                             ("lask5-test", "lask5")]:
                s.discovery.on_announce(
                    {"v": "1.0", "type": "announce", "id": did, "role": "source",
                     "dev": dev, "caps": [], "matrix": [15, 4],
                     "services": {"cmd": 8001}}, "127.0.0.1")
            s.discovery.set_role("fg-left", "left")
            s.discovery.set_role("fg-right", "right")
            s.discovery.set_role("lask5-test", "labeler")

            rec = s.start_multiband_recording(filename="mbflow.csv")
            s.stop_recording()
            assert rec.sensors == {"fg-left": "left", "fg-right": "right"}
            assert rec.label_device_id == "lask5-test"

    def test_start_multiband_requires_tagged_bands(self):
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            s.discovery.auto_subscribe = False
            s.discovery.cache_path = str(Path(d) / "disc.json")
            s._handle_packet(_flexgrid_packet(device_id="fg-untagged"))
            # No role tags set -> refuse rather than guess.
            with __import__("pytest").raises(RuntimeError):
                s.start_multiband_recording(filename="x.csv")

    def test_imu_columns_recorded_sensor_and_labeler(self):
        # data.imu on the sensor band -> imu_* columns; data.imu on the matched
        # labeler (LASK5 gyro = supination ground-truth) -> lbl_imu_* columns.
        def _fg(recv_time, imu):
            return OpenMusclePacket(
                version=CURRENT_VERSION, device_type="flexgrid",
                device_id="fg-imu", timestamp_ms=int(recv_time * 1000),
                data={"matrix": [[10, 11, 12, 13] for _ in range(15)],
                      "rows": 4, "cols": 15, "imu": imu},
                receive_time=recv_time)

        def _lask5(recv_time, imu):
            return OpenMusclePacket(
                version=CURRENT_VERSION, device_type="lask5",
                device_id="lask5-imu", timestamp_ms=int(recv_time * 1000),
                data={"values": [100, 200, 300, 400], "imu": imu},
                receive_time=recv_time)

        s_imu = {"gyro": [1, 2, 3], "accel": [4, 5, 6]}
        l_imu = {"gyro": [7, 8, 9], "accel": [10, 11, 12]}
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)
            s._handle_packet(_fg(time.time(), s_imu))
            s._handle_packet(_lask5(time.time(), l_imu))
            rec = s.start_recording(filename="imu.csv", label_count=4)
            assert rec.label_device_id == "lask5-imu"   # auto-picked labeler
            t = time.time()
            s._handle_packet(_lask5(t, l_imu))           # label in window
            s._handle_packet(_fg(t + 0.001, s_imu))      # sensor pairs with it
            s.stop_recording()

            with open(tmp / "imu.csv") as f:
                rows = list(csv.reader(f))
            header, data = rows[0], rows[1:]
            assert data, "expected a paired row"
            gi = header.index("imu_gx")
            assert data[0][gi:gi + 6] == ["1", "2", "3", "4", "5", "6"]
            li = header.index("lbl_imu_gx")
            assert data[0][li:li + 6] == ["7", "8", "9", "10", "11", "12"]
            assert s.read_capture_meta("imu.csv")["auto"]["imu_columns"] is True

    def test_imu_columns_opt_out(self):
        # with_imu=False keeps the legacy schema-v2 columns (no imu_*).
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)
            s._handle_packet(_flexgrid_packet())
            s._handle_packet(_lask5_packet())
            s.start_recording(filename="noimu.csv", with_imu=False)
            t = time.time()
            s._handle_packet(_lask5_packet(recv_time=t))
            s._handle_packet(_flexgrid_packet(recv_time=t + 0.001))
            s.stop_recording()
            with open(tmp / "noimu.csv") as f:
                header = next(csv.reader(f))
            assert "imu_gx" not in header
            assert s.read_capture_meta("noimu.csv")["auto"]["imu_columns"] is False

    def test_flexgrid_data_imu_in_snapshot(self):
        # Fast IMU path: data.imu={gyro,accel} on a flexgrid frame surfaces as
        # device.imu in the WS snapshot (drives the gyro/orientation widgets).
        with tempfile.TemporaryDirectory() as d:
            s = _make_state(Path(d))
            pkt = OpenMusclePacket(
                version=CURRENT_VERSION, device_type="flexgrid",
                device_id="fg-imu", timestamp_ms=0,
                data={"matrix": [[1, 2, 3, 4] for _ in range(15)],
                      "imu": {"gyro": [10, -20, 3], "accel": [-440, 0, 2150]}},
                receive_time=time.time(),
            )
            s._handle_packet(pkt)
            dev = next(x for x in s._snapshot()["devices"]
                       if x["device_id"] == "fg-imu")
            assert dev["imu"] == {"gyro": [10, -20, 3], "accel": [-440, 0, 2150]}
            # A later frame without imu must not clobber the last-known value.
            s._handle_packet(_flexgrid_packet(device_id="fg-imu"))
            dev = next(x for x in s._snapshot()["devices"]
                       if x["device_id"] == "fg-imu")
            assert dev["imu"]["accel"] == [-440, 0, 2150]
