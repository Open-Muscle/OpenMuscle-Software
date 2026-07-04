"""list_captures() dashboard summary fields (UI-overhaul backend prereq).

The Studio Data view's filter bar needs three per-row fields without
re-reading every CSV:
  - label_source: wire vocabulary ("lask5" / "quest") for the VR/LASK5 chips
  - rows / duration_s: persisted by stop_recording() into meta auto.final,
    used for junk detection (short takes hide by default)
  - created_ms: recording-time epoch ms, preferred over file mtime for the
    date chips (OneDrive sync can rewrite mtimes)

Legacy captures (no meta sidecar, or meta without auto.final) must return
None for all of these so the frontend falls back to size_bytes / mtime.

Same AppState-without-listener harness as test_quest_recording.py.
"""

import time
import tempfile
from pathlib import Path

from openmuscle.web.state import AppState
from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION


def _make_state(tmp):
    # udp_port is never bound (we don't call .start()); high port anyway.
    return AppState(udp_port=53998, captures_dir=str(tmp))


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


def _quest_payload(n_joints=25, handedness="right", device_id="quest-right"):
    joints = [
        {"name": f"j{i}", "pos": [i * 0.01, i * 0.02, i * 0.03],
         "rot": [0, 0, 0, 1], "valid": True}
        for i in range(n_joints)
    ]
    return {"device_id": device_id, "ts": 0, "handedness": handedness,
            "joints": joints}


class TestCaptureListFields:
    def test_recorded_capture_carries_summary_fields(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)
            s._handle_packet(_flexgrid_packet())
            s.ingest_quest_packet(_quest_payload())

            s.start_recording(filename="fields.csv")
            t = time.time()
            s.ingest_quest_packet(_quest_payload())
            s._handle_packet(_flexgrid_packet(recv_time=t + 0.001))
            time.sleep(0.005)
            result = s.stop_recording()

            row = next(r for r in s.list_captures() if r["name"] == "fields.csv")
            # Quest label -> wire vocabulary "quest" (top-level interop key)
            assert row["label_source"] == "quest"
            # Final stats match what stop_recording reported
            assert row["rows"] == result["rows"]
            assert row["duration_s"] == result["duration_s"]
            # created_ms seeded at start (epoch ms, sane range)
            assert isinstance(row["created_ms"], int)
            assert abs(row["created_ms"] / 1000.0 - t) < 60

            # And the sidecar's seeded auto fields survived the final-stats
            # merge (write_capture_meta's shallow auto update must not clobber)
            meta = s.read_capture_meta("fields.csv")
            assert meta["auto"]["sensor_device_id"] == "fg-test"
            assert meta["auto"]["final"]["rows"] == result["rows"]
            assert meta["auto"]["final"]["match_rate"] == result["match_rate"]

    def test_legacy_capture_returns_nulls(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _make_state(tmp)
            # Bare CSV with no .meta.json sidecar = a pre-overhaul capture.
            (tmp / "legacy.csv").write_text("ts_hub_ms,role,device_id\n1,left,x\n")

            row = next(r for r in s.list_captures() if r["name"] == "legacy.csv")
            assert row["label_source"] is None
            assert row["created_ms"] is None
            assert row["rows"] is None
            assert row["duration_s"] is None
            # Existing fields still present for the size-based junk fallback
            assert row["size_bytes"] > 0
            assert row["hands"] in (1, 2)
