"""Closing the window, Ctrl+C or a crash in the middle of a recording must leave
a readable take that is listed with its real row count."""

import csv
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from openmuscle.data.storage import CaptureWriter

from .export_fixtures import begin_take, make_state, record_take


def _csv_rows(path):
    with open(path, newline="") as fh:
        return list(csv.reader(fh))


def test_writer_rows_reach_disk_before_close(tmp_path):
    p = tmp_path / "w.csv"
    w = CaptureWriter(str(p), matrix_rows=4, matrix_cols=15, label_count=2,
                      schema_version="v2")
    for i in range(7):
        w.write_row_v2(1000 + i, "left", "fg-1", [i] * 60, [i, i])
    # Not closed, not flushed by us: this is what a killed process leaves behind.
    rows = _csv_rows(p)
    assert len(rows) == 1 + 7 and rows[0][0] == "ts_hub_ms"
    w.close()
    w.close()                                             # closing twice is harmless
    assert w.row_count == 7


def test_writer_flush_every_n_rows(tmp_path):
    p = tmp_path / "n.csv"
    w = CaptureWriter(str(p), matrix_rows=4, matrix_cols=15, label_count=1,
                      schema_version="v2", flush_every=5)
    for i in range(7):
        w.write_row_v2(i, "left", "fg-1", [0] * 60, [0])
    assert len(_csv_rows(p)) == 1 + 5                     # header + the first flushed batch
    w.close()
    assert len(_csv_rows(p)) == 1 + 7


def test_stop_finishes_a_recording_in_progress(tmp_path):
    """Server exit (Ctrl+C or SIGTERM) calls AppState.stop() mid-recording."""
    state = make_state(tmp_path / "captures")
    rec = begin_take(state, "mid.csv", frames=40)
    state.stop()
    assert state.recording is None
    meta = state.read_capture_meta("mid.csv")
    fin = meta["auto"]["final"]
    assert fin["rows"] == len(_csv_rows(rec.path)) - 1 > 0
    assert fin["verdict"] in ("good", "check", "bad") and "recovered" not in fin
    row = next(r for r in state.list_captures() if r["name"] == "mid.csv")
    assert row["rows"] == fin["rows"]


def test_stop_still_shuts_down_when_finishing_fails(tmp_path, monkeypatch):
    state = make_state(tmp_path / "captures")
    begin_take(state, "boom.csv", frames=10)

    def explode():
        raise OSError("disk went away")
    monkeypatch.setattr(state, "stop_recording", explode)
    state.stop()                                           # must not raise


def test_abruptly_killed_process_leaves_a_readable_listed_take(tmp_path):
    cap = tmp_path / "captures"
    home = tmp_path / "home"
    script = textwrap.dedent("""
        import os, sys, time
        sys.path.insert(0, {root!r})
        from tests.export_fixtures import begin_take, make_state
        state = make_state({cap!r}, port=53972)
        begin_take(state, "killed.csv", frames=60)
        os._exit(1)        # no stop(), no atexit, no close(): like a closed window
    """).format(root=str(Path(__file__).resolve().parents[1]), cap=str(cap))
    env = dict(os.environ, OPENMUSCLE_HOME=str(home),
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    r = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 1, r.stderr

    csv_path = cap / "killed.csv"
    rows = _csv_rows(csv_path)
    assert len(rows) > 1 and rows[0][0] == "ts_hub_ms"     # readable, header + data
    meta = json.loads((cap / "killed.meta.json").read_text())
    assert "final" not in meta["auto"]                     # the stop never ran

    # Next start: the take is listed, then repaired from the CSV.
    state = make_state(cap)
    assert "killed.csv" in [c["name"] for c in state.list_captures()]
    assert state.recover_unfinished_captures() == ["killed.csv"]
    fin = state.read_capture_meta("killed.csv")["auto"]["final"]
    assert fin["recovered"] is True
    assert fin["rows"] == len(rows) - 1
    assert fin["verdict"] in ("good", "check", "bad")
    row = next(c for c in state.list_captures() if c["name"] == "killed.csv")
    assert row["rows"] == len(rows) - 1 and row["quality"] is not None
    assert state.recover_unfinished_captures() == []       # only once


def test_half_written_last_row_is_cut_off_by_recovery(tmp_path):
    state = make_state(tmp_path / "captures")
    rec = begin_take(state, "torn.csv", frames=30)
    full = len(_csv_rows(rec.path)) - 1
    rec.writer._file.write("12345,left,fg-1,9,9,9")        # a row cut off mid-line
    rec.writer.flush()
    state.recording = None                                 # forget it without finishing
    fresh = make_state(tmp_path / "captures")
    assert fresh.recover_unfinished_captures() == ["torn.csv"]
    rows = _csv_rows(rec.path)
    assert len(rows) - 1 == full
    assert all(len(r) == len(rows[0]) for r in rows)       # rectangular again
    assert fresh.read_capture_meta("torn.csv")["auto"]["final"]["rows"] == full


def test_recovery_leaves_finished_and_active_takes_alone(tmp_path):
    state = make_state(tmp_path / "captures")
    record_take(state, "done.csv")
    before = state.read_capture_meta("done.csv")
    live = begin_take(state, "live.csv", frames=10)
    assert state.recover_unfinished_captures() == []
    assert state.read_capture_meta("done.csv")["auto"]["final"] == before["auto"]["final"]
    assert "final" not in state.read_capture_meta("live.csv")["auto"]
    state.stop_recording()
    assert live.path.exists()


def test_recovery_ignores_files_not_written_by_this_recorder(tmp_path):
    state = make_state(tmp_path / "captures")
    (state.captures_dir / "imported.csv").write_text("a,b\n1,2\n")
    assert state.recover_unfinished_captures() == []
    assert state.read_capture_meta("imported.csv") == {}
