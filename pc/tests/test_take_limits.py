"""Short takes stay listed and tagged; one module holds the minimum length.

The 6 s take below is saved, appears in list_captures flagged too_short with a
note that states the agreed minimum and the recommended length, and the stop
result carries the same, so the UI can tag it and keep it out of the one-click
Train selection. The word the UI used for these before is gone from the page.
"""

import time
from pathlib import Path

from openmuscle import quality, take_limits
from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION
from openmuscle.web.state import AppState

STATIC = Path(__file__).resolve().parents[1] / "src" / "openmuscle" / "web" / "static"


def _state(tmp):
    return AppState(udp_port=53992, captures_dir=str(tmp), enable_discovery=False)


def _band(t, i):
    swing = (i % 7) * 30
    matrix = [[100 + swing + c for _ in range(4)] for c in range(15)]
    return OpenMusclePacket(version=CURRENT_VERSION, device_type="flexgrid",
                            device_id="fg-1", timestamp_ms=int(t * 1000),
                            data={"matrix": matrix, "rows": 4, "cols": 15},
                            receive_time=t)


def _labeler(t, i):
    v = (i % 7) * 100
    return OpenMusclePacket(version=CURRENT_VERSION, device_type="lask5",
                            device_id="lask5-1", timestamp_ms=int(t * 1000),
                            data={"values": [v, v + 1, v + 2, v + 3]},
                            receive_time=t)


def _record(state, name, seconds, frames=80, spacing=0.01):
    t0 = time.time()
    state._handle_packet(_band(t0, 0))
    state._handle_packet(_labeler(t0, 0))
    rec = state.start_recording(sensor_device_id="fg-1", label_device_id="lask5-1",
                                filename=name)
    for i in range(frames):
        t = t0 + spacing * (i + 1)
        state._handle_packet(_labeler(t, i))
        state._handle_packet(_band(t + 0.001, i))
    rec.started_at -= seconds
    return state.stop_recording()


# ----- the module -----

def test_limits_and_wording():
    assert take_limits.MIN_TAKE_SECONDS == 10.0
    assert take_limits.RECOMMENDED_TAKE_SECONDS == 30.0
    assert take_limits.is_too_short(rows=500, duration_s=6.0)
    assert take_limits.is_too_short(rows=20, duration_s=30.0)
    assert not take_limits.is_too_short(rows=500, duration_s=10.0)
    assert not take_limits.is_too_short(rows=500, duration_s=45.0)
    # No stored stats: judged by file size.
    assert take_limits.is_too_short(size_bytes=1000)
    assert not take_limits.is_too_short(size_bytes=500000)
    assert not take_limits.is_too_short()
    note = take_limits.short_note(rows=120, duration_s=6.2)
    assert note == ("Too short to train on (6 s). "
                    "Record at least 10 s; 30 s or more works best.")
    assert "12 rows" in take_limits.short_note(rows=12, duration_s=30.0)
    assert take_limits.short_note().startswith("Too short to train on.")


def test_quality_reads_the_shared_numbers():
    assert quality.MIN_SECONDS == take_limits.MIN_TAKE_SECONDS
    assert quality.MIN_ROWS == take_limits.MIN_TAKE_ROWS
    q = quality.evaluate_take({"duration_s": 6.2, "rows": 120})
    reason = q["reasons"][0]
    assert reason.startswith("Only 6 seconds recorded")
    assert "at least 10 s" in reason and "30 s or more" in reason


# ----- a 6 s take -----

def test_six_second_take_is_listed_and_tagged(tmp_path):
    s = _state(tmp_path)
    result = _record(s, "six", seconds=6.0)
    assert result["too_short"] is True
    assert result["short_note"].startswith("Too short to train on (6 s)")
    assert result["limits"] == take_limits.as_dict()
    row = [c for c in s.list_captures() if c["name"] == "six.csv"][0]
    assert row["too_short"] is True
    assert "at least 10 s" in row["short_note"] and "30 s" in row["short_note"]


def test_long_take_is_not_tagged(tmp_path):
    s = _state(tmp_path)
    result = _record(s, "long", seconds=40.0, frames=120)
    assert result["too_short"] is False and result["short_note"] is None
    row = [c for c in s.list_captures() if c["name"] == "long.csv"][0]
    assert row["too_short"] is False and row["short_note"] is None


def test_legacy_take_without_stats_is_judged_by_size(tmp_path):
    s = _state(tmp_path)
    (tmp_path / "tiny.csv").write_text("a,b\n1,2\n")
    (tmp_path / "big.csv").write_text("a,b\n" + "1,2\n" * 30000)
    rows = {c["name"]: c for c in s.list_captures()}
    assert rows["tiny.csv"]["too_short"] is True
    assert rows["big.csv"]["too_short"] is False


def test_live_payload_carries_the_limits(tmp_path):
    s = _state(tmp_path)
    t0 = time.time()
    s._handle_packet(_band(t0, 0))
    s._handle_packet(_labeler(t0, 0))
    s.start_recording(sensor_device_id="fg-1", label_device_id="lask5-1",
                      filename="live")
    tick = s._snapshot()
    assert tick["recording"]["limits"] == take_limits.as_dict()
    s.stop_recording()


def test_trainer_reports_short_takes(tmp_path):
    s = _state(tmp_path)
    _record(s, "six", seconds=6.0)
    # A lone 6 s take is refused before training (too few samples), so train
    # it together with a long take and check the short one is reported.
    _record(s, "long", seconds=40.0, frames=400, spacing=0.1)   # 40 s of timestamps
    res = s.train_from_captures(["six.csv", "long.csv"], n_estimators=4, activate=False)
    assert res["short_captures"] == ["six.csv"]


# ----- wording -----

def test_the_old_word_is_gone_from_the_page():
    for name in ("index.html", "app.js", "styles.css", "rec-status.js"):
        text = (STATIC / name).read_text(encoding="utf-8").lower()
        assert "junk" not in text, name
