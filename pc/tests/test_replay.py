"""Tests for capture replay reconstruction (simulate/replay.py).

Replay re-feeds a recorded capture into the live pipeline so the whole
record/train/predict flow can be tested with no hardware. The load-bearing
parts are: parse v1 + v2 layouts, un-flatten R{r}C{c} row-major back to the
on-wire [cols][rows] matrix, and rebuild Quest joints from the label_* block.
"""

import csv
from pathlib import Path

from openmuscle.simulate.replay import (
    parse_capture, _flexgrid_packet, _quest_payload, _grid_dims,
)


def _write(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# 2x2 grid (rows=2, cols=2). Writer flattens row-major: R0C0,R0C1,R1C0,R1C1 =
# mat[0][0], mat[1][0], mat[0][1], mat[1][1]. So values [10,20,30,40] must
# un-flatten to the on-wire [cols][rows] matrix [[10,30],[20,40]].
def test_grid_dims():
    assert _grid_dims(["timestamp", "R0C0", "R0C1", "R1C0", "R1C1", "label_0"]) == (2, 2)
    assert _grid_dims(["ts_hub_ms", "role", "R0C0", "R3C14"]) == (4, 15)


def test_v1_parse_and_matrix_unflatten(tmp_path):
    p = tmp_path / "v1.csv"
    header = ["timestamp", "R0C0", "R0C1", "R1C0", "R1C1"] + [f"label_{i}" for i in range(14)]
    # one joint row: 14 labels = 2 joints x 7 channels
    labels = [float(i) for i in range(14)]
    _write(p, header, [[1.5, 10, 20, 30, 40] + labels])
    frames, info = parse_capture(str(p))
    assert info["is_v2"] is False
    assert info["grid"] == [2, 2]
    assert len(frames) == 1
    fr = frames[0]
    assert fr["t_ms"] == 1500.0                      # v1 seconds -> ms
    assert fr["device_id"] == "flexgrid-replay"
    assert fr["matrix"] == [[10, 30], [20, 40]]      # [cols][rows], un-flattened
    assert fr["joints"] == [[0, 1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12, 13]]


def test_v2_bilateral_parse(tmp_path):
    p = tmp_path / "v2.csv"
    header = (["ts_hub_ms", "role", "device_id", "R0C0", "R0C1", "R1C0", "R1C1"]
              + [f"label_{i}" for i in range(7)])
    rows = [
        [1000, "left",  "fg-l", 1, 2, 3, 4] + [0.1] * 7,
        [1007, "right", "fg-r", 5, 6, 7, 8] + [0.2] * 7,
        [1033, "left",  "fg-l", 9, 9, 9, 9] + [0.3] * 7,
    ]
    _write(p, header, rows)
    frames, info = parse_capture(str(p))
    assert info["is_v2"] is True
    assert info["roles"] == ["left", "right"]
    assert info["device_ids"] == ["fg-l", "fg-r"]
    assert info["has_quest"] is True
    assert [f["role"] for f in frames] == ["left", "right", "left"]   # time-ordered
    assert frames[0]["device_id"] == "fg-l"
    assert frames[1]["matrix"] == [[5, 7], [6, 8]]


def test_flexgrid_packet_shape():
    pkt = _flexgrid_packet("fg-l", [[1, 3], [2, 4]], [2, 2], 1234)
    assert pkt["type"] == "flexgrid" and pkt["id"] == "fg-l"
    assert pkt["data"]["matrix"] == [[1, 3], [2, 4]]
    assert pkt["data"]["rows"] == 2 and pkt["data"]["cols"] == 2


def test_quest_payload_side_mapping():
    joints = [[0, 1, 2, 0, 0, 0, 1]] * 25
    left = _quest_payload(joints, "left", 10)
    right = _quest_payload(joints, "right", 10)
    none = _quest_payload(joints, "", 10)
    assert left["device_id"] == "quest-left" and left["handedness"] == "left"
    assert right["device_id"] == "quest-right" and right["handedness"] == "right"
    assert none["device_id"] == "quest-replay"            # untagged fallback
    assert left["joints"][0]["pos"] == [0, 1, 2]
    assert left["joints"][0]["rot"] == [0, 0, 0, 1]
    assert left["joints"][5]["name"] == "index-finger-metacarpal"   # JOINT_NAMES order


def test_empty_capture_returns_no_frames(tmp_path):
    p = tmp_path / "empty.csv"
    _write(p, ["timestamp", "R0C0", "R0C1", "R1C0", "R1C1", "label_0"], [])
    frames, info = parse_capture(str(p))
    assert frames == [] and info["rows"] == 0
