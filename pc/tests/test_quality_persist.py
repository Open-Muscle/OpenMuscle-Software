"""Take quality is computed server-side at stop, persisted into the capture's
meta (auto.final), exposed by list_captures, and survives a restart. Legacy
captures with no stored verdict are scored by a one-time CSV scan.

Same AppState-without-listener harness as test_capture_list_fields.py."""

import pytest
import asyncio
import shutil
import time
from pathlib import Path

from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION
from openmuscle.web.state import AppState

pytestmark = pytest.mark.usefixtures("tiny_training_ok")

FIXTURE = Path(__file__).parent / "fixtures" / "capture_1779731309_dud.csv"


def _state(tmp):
    return AppState(udp_port=53997, captures_dir=str(tmp), enable_discovery=False)


def _band(t, engaged, i):
    # [cols][rows] matrix as the receiver delivers it (15 cols of 4 rows).
    # Engaged: every cell swings well past the flat threshold. Flat: constant.
    swing = (i % 7) * 30 if engaged else 0
    matrix = [[100 + swing + c for _ in range(4)] for c in range(15)]
    return OpenMusclePacket(version=CURRENT_VERSION, device_type="flexgrid",
                            device_id="fg-1", timestamp_ms=int(t * 1000),
                            data={"matrix": matrix, "rows": 4, "cols": 15},
                            receive_time=t)


def _labeler(t, moving, i):
    v = (i % 7) * 100 if moving else 500
    return OpenMusclePacket(version=CURRENT_VERSION, device_type="lask5",
                            device_id="lask5-1", timestamp_ms=int(t * 1000),
                            data={"values": [v, v + 1, v + 2, v + 3]},
                            receive_time=t)


def _record_take(state, name, engaged=True, labeler_moving=True,
                 frames=80, seconds=30.0):
    t0 = time.time()
    state._handle_packet(_band(t0, engaged, 0))
    state._handle_packet(_labeler(t0, labeler_moving, 0))
    rec = state.start_recording(sensor_device_id="fg-1", label_device_id="lask5-1",
                                filename=name)
    for i in range(frames):
        t = t0 + 0.01 * (i + 1)
        state._handle_packet(_labeler(t, labeler_moving, i))
        state._handle_packet(_band(t + 0.001, engaged, i))
    rec.started_at -= seconds          # pretend the take ran this long
    return state.stop_recording()


def test_engaged_take_is_good_and_persisted(tmp_path):
    s = _state(tmp_path)
    result = _record_take(s, "good.csv")
    assert result["quality"]["verdict"] == "good", result["quality"]
    fin = s.read_capture_meta("good.csv")["auto"]["final"]
    assert fin["verdict"] == "good"
    assert fin["flat_cells"] == 0 and fin["cells_total"] == 60
    assert fin["label_cols_moved"] == fin["label_cols"] == 4
    assert fin["band_flat"] is False
    row = next(r for r in s.list_captures() if r["name"] == "good.csv")
    assert row["quality"]["verdict"] == "good"
    assert row["quality"]["reasons"] == []


def test_flat_band_take_is_bad_and_survives_restart(tmp_path):
    s = _state(tmp_path)
    result = _record_take(s, "flat.csv", engaged=False)
    assert result["quality"]["verdict"] == "bad"
    assert any("Band did not move: 60 of 60 cells flat" in r
               for r in result["quality"]["reasons"]), result["quality"]
    # A brand-new AppState on the same folder = server restart.
    s2 = _state(tmp_path)
    row = next(r for r in s2.list_captures() if r["name"] == "flat.csv")
    assert row["quality"]["verdict"] == "bad"
    assert "weak_signal" in [f["code"] for f in row["quality"]["flags"]]
    assert "Was it on the arm?" in row["quality"]["reasons"][0]


def test_idle_labeler_is_flagged_check(tmp_path):
    s = _state(tmp_path)
    result = _record_take(s, "idle.csv", labeler_moving=False)
    q = result["quality"]
    assert q["verdict"] == "check", q
    assert [f["code"] for f in q["flags"]] == ["idle_labeler"]


def test_short_take_is_flagged_check(tmp_path):
    s = _state(tmp_path)
    result = _record_take(s, "short.csv", seconds=0.0)
    q = result["quality"]
    assert q["verdict"] == "check", q
    assert "short" in [f["code"] for f in q["flags"]]
    assert "seconds recorded" in " ".join(q["reasons"])


def test_live_snapshot_carries_server_verdict(tmp_path):
    s = _state(tmp_path)
    t0 = time.time()
    s._handle_packet(_band(t0, False, 0))
    s._handle_packet(_labeler(t0, True, 0))
    s.start_recording(sensor_device_id="fg-1", label_device_id="lask5-1",
                      filename="live.csv")
    for i in range(40):
        t = t0 + 0.01 * (i + 1)
        s._handle_packet(_labeler(t, True, i))
        s._handle_packet(_band(t + 0.001, False, i))
    snap = s._snapshot()["recording"]
    assert snap["band_flat"] is True
    assert snap["quality"]["verdict"] == "bad"
    # A just-started take is not nagged about being short.
    assert "short" not in [f["code"] for f in snap["quality"]["flags"]]
    s.stop_recording()


def test_legacy_dud_fixture_scanned_via_endpoint_is_bad(tmp_path):
    from openmuscle.web.app import create_app
    cap_dir = tmp_path / "captures"
    cap_dir.mkdir()
    shutil.copy(FIXTURE, cap_dir / FIXTURE.name)
    app = create_app(udp_port=53996, captures_dir=str(cap_dir))
    route = next(r for r in app.routes
                 if getattr(r, "path", "") == "/api/captures/{name}/quality")

    # Not scored yet: the listing says so (UI offers a "scan" button).
    state = AppState(udp_port=53995, captures_dir=str(cap_dir), enable_discovery=False)
    assert state.list_captures()[0]["quality"] is None

    q = asyncio.run(route.endpoint(name=FIXTURE.name, refresh=False))
    assert q["verdict"] == "bad"
    assert q["source"] == "scan"
    assert "Band did not move" in q["reasons"][0]
    # Cached into the sidecar: a second call (and the listing) read it back.
    assert asyncio.run(route.endpoint(name=FIXTURE.name, refresh=False))["source"] == "stored"
    assert state.list_captures()[0]["quality"]["verdict"] == "bad"


def test_quality_endpoint_404_for_unknown_capture(tmp_path):
    from fastapi import HTTPException
    from openmuscle.web.app import create_app
    app = create_app(udp_port=53994, captures_dir=str(tmp_path))
    route = next(r for r in app.routes
                 if getattr(r, "path", "") == "/api/captures/{name}/quality")
    try:
        asyncio.run(route.endpoint(name="nope.csv", refresh=False))
    except HTTPException as e:
        assert e.status_code == 404
    else:
        raise AssertionError("expected 404")


def _write_csv(path, flat, n=40):
    """Tiny schema-v2 take (one left band, 6 cells, 2 labels). flat=True holds
    every cell constant, the dud pattern."""
    import csv
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_hub_ms", "role", "device_id"]
                   + ["R0C%d" % c for c in range(6)] + ["label_0", "label_1"])
        for i in range(n):
            feats = [2.0 for _ in range(6)] if flat else [2.0 + ((i + c) % 7) * 3 for c in range(6)]
            w.writerow([1_000_000 + 40 * i, "left", "fg-left"] + feats
                       + [(i % 5) * 0.1, (i % 3) * 0.1])


def test_train_accepts_bad_takes_but_lists_them(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)          # ModelRegistry writes CWD-relative
    caps = tmp_path / "captures"
    caps.mkdir()
    s = AppState(udp_port=53880, captures_dir=str(caps), enable_discovery=False)
    _write_csv(caps / "ok.csv", flat=False)
    _write_csv(caps / "flat.csv", flat=True)

    result = s.train_from_captures(["ok.csv", "flat.csv"], n_estimators=4,
                                   activate=False, role="left")
    assert result["model_path"]                    # it still trained
    assert [b["name"] for b in result["bad_captures"]] == ["flat.csv"]
    assert "Band did not move" in result["bad_captures"][0]["reasons"][0]

    clean = s.train_from_captures(["ok.csv"], n_estimators=4,
                                  activate=False, role="left")
    assert clean["bad_captures"] == []
