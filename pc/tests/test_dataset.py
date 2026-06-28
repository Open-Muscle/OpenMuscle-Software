"""Tests for schema-v2 trainer loading: the bilateral long->wide pivot and the
role-guard that stops a multi-role capture from silently pooling L+R into a
single role-agnostic feature set (board #0072/#0073, PROTOCOL.md 8.4).
"""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from openmuscle.data.dataset import (
    is_bilateral_v2, pivot_bilateral, load_training_data, detect_columns,
)


COLUMNS = ["ts_hub_ms", "role", "device_id",
           "R0C0", "R0C1", "R1C0", "R1C1", "label_0", "label_1"]

# Mirrors the bilateral byte golden (2x2 bands, row-major features already flat).
BILATERAL_ROWS = [
    (1718000000000, "left",  "fg-left",  12, 18, 20, 25, 1.0, 0.5),
    (1718000000007, "right", "fg-right", 30, 28, 22, 19, 1.0, 0.5),
    (1718000000033, "left",  "fg-left",  13, 19, 21, 24, 0.8, 0.5),
    (1718000000040, "right", "fg-right", 31, 27, 23, 18, 0.8, 0.5),
]


def _df(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


# ---- role detection -------------------------------------------------------

def test_is_bilateral_v2_true_for_left_and_right():
    assert is_bilateral_v2(_df(BILATERAL_ROWS)) is True


def test_is_bilateral_v2_false_for_single_role():
    single = _df([r for r in BILATERAL_ROWS if r[1] == "left"])
    assert is_bilateral_v2(single) is False


def test_is_bilateral_v2_false_for_v1_no_role():
    v1 = pd.DataFrame([[0.0, 1, 2, 3, 4, 0.1, 0.2]],
                      columns=["timestamp", "R0C0", "R0C1", "R1C0", "R1C1",
                               "label_0", "label_1"])
    assert is_bilateral_v2(v1) is False


# ---- pivot ----------------------------------------------------------------

def test_pivot_pairs_nearest_and_concats_left_then_right():
    X, y = pivot_bilateral(_df(BILATERAL_ROWS), window_ms=50)
    # left@0 <-> right@7 ; left@33 <-> right@40
    assert list(X.columns) == ["R0C0_L", "R0C1_L", "R1C0_L", "R1C1_L",
                               "R0C0_R", "R0C1_R", "R1C0_R", "R1C1_R"]
    assert X.shape == (2, 8)
    assert list(X.iloc[0]) == [12, 18, 20, 25, 30, 28, 22, 19]
    assert list(X.iloc[1]) == [13, 19, 21, 24, 31, 27, 23, 18]
    # labels come from the left row (labeler value nearest the group ts)
    assert list(y.iloc[0]) == [1.0, 0.5]
    assert list(y.iloc[1]) == [0.8, 0.5]


def test_pivot_drops_unpaired_left():
    rows = [
        (1000, "left",  "l", 1, 1, 1, 1, 0.1, 0.2),   # pairs with right@1005
        (1005, "right", "r", 2, 2, 2, 2, 0.1, 0.2),
        (1100, "left",  "l", 9, 9, 9, 9, 0.3, 0.4),   # no right within 50ms -> dropped
        (1200, "right", "r", 3, 3, 3, 3, 0.3, 0.4),
    ]
    X, y = pivot_bilateral(_df(rows), window_ms=50)
    assert X.shape == (1, 8)              # the unpaired left@1100 dropped
    assert list(X.iloc[0]) == [1, 1, 1, 1, 2, 2, 2, 2]


def test_pivot_feature_width_doubles():
    # 4 features/band -> 8 wide. (Real 15x4 bands: 60 -> 120.)
    X, _ = pivot_bilateral(_df(BILATERAL_ROWS), window_ms=50)
    single_feats, _ = detect_columns(_df(BILATERAL_ROWS))
    assert X.shape[1] == 2 * len(single_feats)


# ---- load_training_data routing + the guard -------------------------------

def _write_csv(path, rows, columns=COLUMNS):
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def test_load_training_data_pivots_bilateral_not_pools():
    # The GUARD: a bilateral capture must become the WIDE matrix (8 cols),
    # never the pooled single-source set (4 cols).
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bilateral.csv"
        _write_csv(p, BILATERAL_ROWS)
        X, y = load_training_data(str(p))
    assert X.shape == (2, 8)             # wide, not 4-wide pooled
    assert "R0C0_L" in X.columns and "R0C0_R" in X.columns


def test_load_training_data_single_role_v2_uses_standard_path():
    # Single-role v2 trains as-is: 4 feature cols, role/device_id/ts ignored.
    single = [r for r in BILATERAL_ROWS if r[1] == "left"]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "single.csv"
        _write_csv(p, single)
        X, y = load_training_data(str(p))
    assert list(X.columns) == ["R0C0", "R0C1", "R1C0", "R1C1"]
    assert list(y.columns) == ["label_0", "label_1"]
    assert X.shape == (2, 4)


# ---- separate-model-per-hand: the --role filter (Tory 2026-06-27) ---------

def test_role_left_keeps_only_left_single_arm():
    # role=left on a two-hand capture -> the LEFT rows as a single-arm matrix
    # (4 cols, NOT the 8-wide bilateral pivot). One model per hand.
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "two_hand.csv"
        _write_csv(p, BILATERAL_ROWS)
        X, y = load_training_data(str(p), role="left")
    assert list(X.columns) == ["R0C0", "R0C1", "R1C0", "R1C1"]   # single-arm width
    assert X.shape == (2, 4)
    assert list(X.iloc[0]) == [12, 18, 20, 25]                   # the left band
    assert list(y.iloc[0]) == [1.0, 0.5]


def test_role_right_keeps_only_right():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "two_hand.csv"
        _write_csv(p, BILATERAL_ROWS)
        X, y = load_training_data(str(p), role="right")
    assert X.shape == (2, 4)
    assert list(X.iloc[0]) == [30, 28, 22, 19]                   # the right band


def test_role_is_case_insensitive():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "two_hand.csv"
        _write_csv(p, BILATERAL_ROWS)
        X, _ = load_training_data(str(p), role="LEFT")
    assert list(X.iloc[0]) == [12, 18, 20, 25]


def test_role_on_csv_without_role_column_raises():
    v1_cols = ["timestamp", "R0C0", "R0C1", "R1C0", "R1C1", "label_0", "label_1"]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "v1.csv"
        _write_csv(p, [[0.0, 1, 2, 3, 4, 0.1, 0.2]], columns=v1_cols)
        with pytest.raises(ValueError, match="no 'role' column"):
            load_training_data(str(p), role="left")


def test_role_with_no_matching_rows_raises():
    left_only = [r for r in BILATERAL_ROWS if r[1] == "left"]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "left_only.csv"
        _write_csv(p, left_only)
        with pytest.raises(ValueError, match="role=right"):
            load_training_data(str(p), role="right")


def test_load_training_data_v1_unchanged():
    v1_cols = ["timestamp", "R0C0", "R0C1", "R1C0", "R1C1", "label_0", "label_1"]
    rows = [[0.0, 1, 2, 3, 4, 0.1, 0.2], [0.03, 5, 6, 7, 8, 0.3, 0.4]]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "v1.csv"
        _write_csv(p, rows, columns=v1_cols)
        X, y = load_training_data(str(p))
    assert list(X.columns) == ["R0C0", "R0C1", "R1C0", "R1C1"]
    assert X.shape == (2, 4)
