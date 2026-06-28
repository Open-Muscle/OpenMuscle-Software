"""End-to-end integration: two-hand record -> per-hand train -> per-hand infer.

The fleet-dark pre-test de-risk. Drives the WHOLE separate-model-per-hand path
with synthetic in-process data (no hardware): record a bilateral session (two
bands + two Quest hands, role-tagged), train model_left + model_right from that
ONE CSV via the --role filter, load both into a fresh AppState, tag the bands
left/right through discovery, feed the two bands, and assert each band's live
prediction comes from its OWN hand's model.

The hands carry distinct markers (left joints at 2.0, right at 100.0), so each
side's label set is a constant the RandomForest learns -> the left band predicts
LOW and the right band HIGH. A shared or mis-routed model would not separate
them, so the inequality is the proof that per-hand routing is wired end to end.
"""

import csv
import tempfile
import time
from pathlib import Path

from openmuscle.web.state import AppState
from openmuscle.ml.training import train_model
from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION


def _fg(device_id, recv_time=None):
    # 15 cols x 4 rows -> 60 features (matches a real V4 band), deterministic.
    matrix = [[10 + c + r for r in range(4)] for c in range(15)]
    return OpenMusclePacket(
        version=CURRENT_VERSION, device_type="flexgrid", device_id=device_id,
        timestamp_ms=0, data={"matrix": matrix, "rows": 4, "cols": 15},
        receive_time=recv_time or time.time())


def _hand(handedness, device_id, marker):
    joints = [{"name": f"j{i}", "pos": [marker, marker, marker],
               "rot": [0, 0, 0, 1]} for i in range(25)]
    return {"device_id": device_id, "handedness": handedness, "joints": joints}


def _announce(did):
    return {"v": "1.0", "type": "announce", "id": did, "role": "source",
            "dev": "flexgrid", "caps": [], "matrix": [15, 4],
            "services": {"cmd": 8001}}


def _record_two_hand(s, filename, n=16):
    s._handle_packet(_fg("fg-left"))
    s._handle_packet(_fg("fg-right"))
    s.ingest_quest_packet(_hand("left", "quest-left", 2.0))
    s.ingest_quest_packet(_hand("right", "quest-right", 100.0))
    s.start_recording(
        filename=filename, sensor_device_id="fg-left", role="left",
        extra_sensors=[{"device_id": "fg-right", "role": "right"}],
        side_labelers={"left": "quest-left", "right": "quest-right"})
    for _ in range(n):
        t = time.time()
        s.ingest_quest_packet(_hand("left", "quest-left", 2.0))
        s.ingest_quest_packet(_hand("right", "quest-right", 100.0))
        s._handle_packet(_fg("fg-left", recv_time=t + 0.001))
        s._handle_packet(_fg("fg-right", recv_time=t + 0.001))
        time.sleep(0.003)
    s.stop_recording()


def test_two_hand_record_train_infer_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)

        # 1) RECORD a two-hand session (bilateral, per-side labels).
        s = AppState(udp_port=53870, captures_dir=str(tmp),
                     enable_discovery=False)
        _record_two_hand(s, "two_hand.csv")
        csv_path = tmp / "two_hand.csv"
        rows = list(csv.reader(open(csv_path)))
        header, data = rows[0], rows[1:]
        role_i = header.index("role")
        assert {r[role_i] for r in data} == {"left", "right"}   # both arms captured
        assert "forearm_roll_deg" in header                     # orientation labels

        # 2) TRAIN one model per hand from that ONE CSV via the --role filter.
        #    Each in its own dir so the engine name (parent-dir) is distinct, as
        #    in the real registry layout (random_forest_left_* / _right_*).
        left_path = str(tmp / "left" / "model.pkl")
        right_path = str(tmp / "right" / "model.pkl")
        train_model(str(csv_path), output=left_path, role="left", n_estimators=8)
        train_model(str(csv_path), output=right_path, role="right", n_estimators=8)

        # 3) INFER: load both, tag the bands left/right via discovery (the real
        #    Sources-panel path), feed both bands, each predicts via its own model.
        s2 = AppState(udp_port=53871, captures_dir=str(tmp),
                      model_left=left_path, model_right=right_path)
        s2.discovery.auto_subscribe = False
        s2.discovery.cache_path = str(tmp / "disc.json")
        s2._handle_packet(_fg("fg-left"))
        s2._handle_packet(_fg("fg-right"))
        for did in ("fg-left", "fg-right"):
            s2.discovery.on_announce(_announce(did), "127.0.0.1")
        s2.discovery.set_role("fg-left", "left")
        s2.discovery.set_role("fg-right", "right")
        s2._snapshot()                       # refresh _role_by_device from the tags
        s2._handle_packet(_fg("fg-left"))
        s2._handle_packet(_fg("fg-right"))
        inf = s2._snapshot()["inference"]

        assert set(inf["by_device"]) == {"fg-left", "fg-right"}
        left_pred = inf["by_device"]["fg-left"]
        right_pred = inf["by_device"]["fg-right"]
        left_mean = sum(left_pred) / len(left_pred)
        right_mean = sum(right_pred) / len(right_pred)
        # Left band ran the model trained on the LEFT hand (marker 2.0 -> low),
        # right band the RIGHT hand model (marker 100.0 -> high). The separation
        # proves each band was routed to its OWN hand's model.
        assert left_mean < right_mean
        assert left_mean < 20 and right_mean > 20
        # The two engines are genuinely distinct models.
        assert s2.engines["left"].name != s2.engines["right"].name
        assert inf["model"] and "left=" in inf["model"] and "right=" in inf["model"]
