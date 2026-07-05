"""Regression: a gamepad capture must size its label columns to the pad's full
axes+buttons width, not the default 4.

Before the fix, start_recording used the default label_count=4, so the CSV
header got 4 label columns (the axes) while every row wrote all 21 values
(4 axes + 17 buttons). pandas then read more fields than header names, shifted
the columns, and training died with "no rows with role=left". This pins the
header width == row width for a controller labeler."""

import csv
import time
from pathlib import Path

from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION
from openmuscle.web.state import AppState


def _fg(did, val):
    # 4x15 flexgrid matrix as [cols][rows] (15 cols of 4), the shape _record_packet expects.
    return OpenMusclePacket(version=CURRENT_VERSION, device_type="flexgrid",
                            device_id=did, timestamp_ms=0,
                            data={"matrix": [[val] * 4 for _ in range(15)]},
                            receive_time=time.time())


def _pad(state, axes, buttons):
    state.ingest_gamepad_packet({"device_id": "gamepad-0", "mapping": "standard",
                                 "axes": axes, "buttons": buttons})


def test_gamepad_recording_header_width(tmp_path):
    s = AppState(udp_port=53995, captures_dir=str(tmp_path), enable_discovery=False)
    s._handle_packet(_fg("fg-L", 100))                      # register the band
    _pad(s, [0.0] * 4, [0.0] * 17)                          # 4 axes + 17 buttons = 21

    rec = s.start_recording(sensor_device_id="fg-L", role="left",
                            label_device_id="gamepad-0")
    # The fix sized the header + lock to the pad's full width.
    assert rec.writer.label_count == 21
    assert rec.locked_label_count == 21

    # Feed a paired frame (label then sensor, close in time -> matches in window).
    _pad(s, [0.5, -0.5, 0.1, 0.2], [1.0] + [0.0] * 16)
    s._handle_packet(_fg("fg-L", 250))
    s.stop_recording()

    with open(rec.path, newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = [r for r in reader]
    label_cols = [c for c in header if c.startswith("label_")]
    assert len(label_cols) == 21, label_cols
    assert label_cols[-1] == "label_20"
    # Every row is rectangular against the header (the original corruption).
    assert rows, "no paired rows were written"
    for r in rows:
        assert len(r) == len(header), (len(r), len(header))
    # role column reads back as the band role, not a shifted sensor value.
    role_i = header.index("role")
    assert all(r[role_i] == "left" for r in rows)


def test_non_gamepad_recording_unaffected(tmp_path):
    # A sensor-only capture (no label device) keeps its zero-label behavior:
    # the gamepad branch must not touch other paths.
    s = AppState(udp_port=53994, captures_dir=str(tmp_path), enable_discovery=False)
    s._handle_packet(_fg("fg-L", 100))
    rec = s.start_recording(sensor_device_id="fg-L", role="left", label_device_id="")
    assert rec.locked_label_count is None
    s.stop_recording()
