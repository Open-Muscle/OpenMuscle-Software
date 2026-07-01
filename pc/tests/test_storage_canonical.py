"""Tests for the opt-in canonical lbl_* label columns (DATA_SCHEMA.md #0302) in
CaptureWriter: they append after the raw label_*/imu/forearm columns, mask absent
DOF, and (being opt-in) leave the default schema-v2 output byte-golden untouched."""

import csv

from openmuscle.data.storage import CaptureWriter


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.reader(f))


def test_canonical_off_by_default(tmp_path):
    out = tmp_path / "a.csv"
    w = CaptureWriter(str(out), matrix_rows=1, matrix_cols=2, label_count=2,
                      schema_version="v2")
    w.write_row_v2(1, "left", "d1", [10, 20], [0.1, 0.2])
    w.close()
    header = read_csv(str(out))[0]
    # No canonical columns unless opted in -> the minimal-v2 byte golden is safe.
    assert not any(c.startswith("lbl_flex_") or c.startswith("lbl_ang_") for c in header)


def test_canonical_columns_appended_and_masked(tmp_path):
    out = tmp_path / "b.csv"
    w = CaptureWriter(str(out), matrix_rows=1, matrix_cols=2, label_count=2,
                      schema_version="v2", with_canonical=True)
    canon = {"lbl_flex_index": 0.5, "lbl_flex_thumb": 0.0, "lbl_ang_index_pip": 90.0}
    w.write_row_v2(1, "left", "d1", [10, 20], [0.1, 0.2], canonical=canon)
    w.close()
    header, data = read_csv(str(out))[:2]
    # 15 canonical columns, in the fixed order, appended AFTER the raw label_*.
    assert header[-15:] == CaptureWriter._CANONICAL_LABEL_COLS
    assert header.index("label_1") < header.index("lbl_flex_thumb")
    row = dict(zip(header, data))
    assert row["lbl_flex_thumb"] == "0.0"       # a real 0.0 (extended) is written
    assert row["lbl_flex_index"] == "0.5"
    assert row["lbl_ang_index_pip"] == "90.0"
    assert row["lbl_ang_pinky_mcp"] == ""        # absent DOF -> blank (NaN/masked)


def test_canonical_none_row_all_masked(tmp_path):
    out = tmp_path / "c.csv"
    w = CaptureWriter(str(out), matrix_rows=1, matrix_cols=2, label_count=2,
                      schema_version="v2", with_canonical=True)
    w.write_row_v2(1, "left", "d1", [10, 20], [0.1, 0.2], canonical=None)
    w.close()
    header, data = read_csv(str(out))[:2]
    row = dict(zip(header, data))
    assert all(row[c] == "" for c in CaptureWriter._CANONICAL_LABEL_COLS)
