"""Pre-train data checks (ml/checks.py) and their wiring into training.

Every way a person can pick unusable recordings gets a specific plain sentence
at block or warn level, before any model is fitted, and nothing a client sees
carries library wording ("n_samples", "Input y contains NaN").
"""

import asyncio
import csv
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from openmuscle.ml import checks as dc
from openmuscle.web.app import create_app
from openmuscle.web.state import AppState, TrainDataError

N_FEATS = 6
STEP_MS = 40            # 25 Hz


def _rows(n, role="left", device="fg-1", t0=1_000_000, flat=False,
          const_labels=False, nan_labels=0, all_nan=False, base=0):
    out = []
    for i in range(n):
        if flat:
            feats = [100.0] * N_FEATS
        else:
            feats = [100.0 + base + 40.0 * math.sin(i / 7.0 + c) for c in range(N_FEATS)]
        if const_labels:
            labels = [0.5, 0.5]
        else:
            labels = [0.5 + 0.4 * math.sin(i / 7.0), 0.5 + 0.4 * math.cos(i / 9.0)]
        if all_nan or i < nan_labels:
            labels = [float("nan"), float("nan")]
        out.append([t0 + i * STEP_MS, role, device] + feats + labels)
    return out


HEADER = (["ts_hub_ms", "role", "device_id"]
          + [f"R0C{c}" for c in range(N_FEATS)] + ["label_0", "label_1"])


def _write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)


def _df(rows):
    return pd.DataFrame(rows, columns=HEADER)


def _codes(found, level=None):
    return [c["code"] for c in found if level is None or c["level"] == level]


def _msg(found, code):
    return next(c["message"] for c in found if c["code"] == code)


# --------------------------------------------------------------- blocks

def test_clean_data_has_no_blocks():
    found = dc.check_training_data(_df(_rows(400)), role="left")
    assert dc.blocks(found) == []


def test_eight_rows_blocks_with_plain_message():
    found = dc.check_training_data(_df(_rows(8)), role="left")
    assert "too_few_rows" in _codes(found, "block")
    msg = _msg(found, "too_few_rows")
    assert "8 usable samples" in msg and "at least 150" in msg
    assert "n_samples" not in msg


def test_short_in_seconds_blocks_even_with_enough_rows():
    # 200 rows at 10 ms is two seconds of data.
    rows = _rows(200)
    for i, r in enumerate(rows):
        r[0] = 1_000_000 + i * 10
    found = dc.check_training_data(_df(rows), role="left")
    assert "too_few_rows" in _codes(found, "block")
    assert "seconds" in _msg(found, "too_few_rows")


def test_constant_labels_block():
    found = dc.check_training_data(_df(_rows(400, const_labels=True)), role="left")
    assert "constant_labels" in _codes(found, "block")
    assert "never moved" in _msg(found, "constant_labels")


def test_flat_band_blocks():
    found = dc.check_training_data(_df(_rows(400, flat=True)), role="left")
    assert "flat_band" in _codes(found, "block")
    assert "not touching the arm" in _msg(found, "flat_band")


def test_all_nan_labels_block():
    found = dc.check_training_data(_df(_rows(400, all_nan=True)), role="left")
    assert _codes(found, "block") == ["labels_all_nan"]
    assert "NaN" not in _msg(found, "labels_all_nan")


def test_some_nan_labels_warn_and_are_counted():
    found = dc.check_training_data(_df(_rows(400, nan_labels=12)), role="left")
    assert dc.blocks(found) == []
    assert "nan_rows_dropped" in _codes(found, "warn")
    assert "12 rows" in _msg(found, "nan_rows_dropped")


def test_no_label_columns_block():
    df = _df(_rows(400)).drop(columns=["label_0", "label_1"])
    found = dc.check_training_data(df)
    assert _codes(found, "block") == ["no_labels"]


def test_role_missing_from_selection_blocks():
    found = dc.check_training_data(_df(_rows(400, role="left")), role="right")
    assert _codes(found, "block") == ["no_rows_for_side"]
    assert "right band" in _msg(found, "no_rows_for_side")


# ------------------------------------------------------------- warnings

def test_single_capture_warns():
    found = dc.check_training_data(_df(_rows(400)), role="left",
                                   meta={"captures": ["a.csv"], "capture_meta": {}})
    assert "single_capture" in _codes(found, "warn")


def test_two_captures_do_not_warn_single():
    df = _df(_rows(400))
    df["_capture"] = ["a.csv"] * 200 + ["b.csv"] * 200
    found = dc.check_training_data(df, role="left")
    assert "single_capture" not in _codes(found)


def test_mixed_band_units_warn():
    rows = _rows(300, device="fg-A") + _rows(300, device="fg-B", t0=9_000_000)
    found = dc.check_training_data(_df(rows), role="left")
    assert "mixed_bands" in _codes(found, "warn")
    assert "fg-A" in _msg(found, "mixed_bands") and "fg-B" in _msg(found, "mixed_bands")


def test_mixed_participants_and_sessions_warn():
    day = 86_400_000
    meta = {"captures": ["a.csv", "b.csv"], "capture_meta": {
        "a.csv": {"created_ms": 1_700_000_000_000,
                  "auto": {"session_id": "s1", "session_wearer": "P-01"}},
        "b.csv": {"created_ms": 1_700_000_000_000 + 3 * day,
                  "auto": {"session_id": "s2", "session_wearer": "p-02"}},
    }}
    df = _df(_rows(400))
    df["_capture"] = ["a.csv"] * 200 + ["b.csv"] * 200
    found = dc.check_training_data(df, role="left", meta=meta)
    assert {"mixed_participants", "mixed_sessions"} <= set(_codes(found, "warn"))
    assert "different participants" in _msg(found, "mixed_participants")


def test_same_participant_different_case_is_not_mixed():
    meta = {"captures": ["a.csv", "b.csv"], "capture_meta": {
        "a.csv": {"auto": {"session_id": "s1", "session_wearer": "P-01"}},
        "b.csv": {"auto": {"session_id": "s1", "session_wearer": " p-01 "}},
    }}
    df = _df(_rows(400))
    df["_capture"] = ["a.csv"] * 200 + ["b.csv"] * 200
    found = dc.check_training_data(df, role="left", meta=meta)
    assert "mixed_participants" not in _codes(found)
    assert "mixed_sessions" not in _codes(found)


def test_left_and_right_from_different_times_is_one_model_per_hand():
    rows = (_rows(300, role="left", device="fg-L", t0=1_000_000)
            + _rows(300, role="right", device="fg-R", t0=900_000_000))
    df = _df(rows)
    df["_capture"] = ["left.csv"] * 300 + ["right.csv"] * 300
    assert dc.plan_sides(dc._roles_by_capture(df)) == {
        "left": ["left.csv"], "right": ["right.csv"]}
    found = dc.check_training_data(df)
    assert "trained_each_side" in _codes(found, "warn")
    assert "different times" in _msg(found, "trained_each_side")
    assert dc.blocks(found) == []


def test_left_right_at_different_times_with_a_bad_side_blocks_that_side():
    rows = (_rows(300, role="left", device="fg-L")
            + _rows(300, role="right", device="fg-R", t0=900_000_000, flat=True))
    df = _df(rows)
    df["_capture"] = ["left.csv"] * 300 + ["right.csv"] * 300
    found = dc.check_training_data(df)
    blocked = dc.blocks(found)
    assert [c["code"] for c in blocked] == ["flat_band"]
    assert blocked[0]["message"].startswith("Right hand:")


def test_one_file_with_both_hands_that_never_overlap_blocks():
    rows = (_rows(200, role="left", t0=1_000_000)
            + _rows(200, role="right", t0=50_000_000))
    found = dc.check_training_data(_df(rows))
    assert _codes(found, "block") == ["no_paired_rows"]


# ------------------------------------------------------- error mapping

@pytest.mark.parametrize("raw", [
    "Found array with 0 sample(s) (shape=(0, 60)) while a minimum of 1 is required.",
    "With n_samples=0, test_size=0.2 and train_size=None, the resulting train set will be empty",
    "Input y contains NaN.",
    "Input X contains infinity or a value too large for dtype('float64').",
    "combine_csvs: refusing to merge captures with different schemas",
    "weird unknown failure inside the forest",
])
def test_friendly_error_never_leaks_library_words(raw):
    msg = dc.friendly_error(ValueError(raw))
    assert msg and "n_samples" not in msg and "Input y contains NaN" not in msg
    assert "sample(s)" not in msg and "dtype" not in msg


# ------------------------------------------------- state / HTTP wiring

@pytest.fixture
def state(tmp_path):
    caps = tmp_path / "captures"
    caps.mkdir()
    return AppState(udp_port=53840, captures_dir=str(caps), enable_discovery=False)


def test_state_blocks_before_fitting(state):
    _write(state.captures_dir / "tiny.csv", _rows(8))
    with pytest.raises(TrainDataError) as ei:
        state.train_from_captures(["tiny.csv"], n_estimators=4, role="left")
    assert "at least 150" in str(ei.value)
    assert "n_samples" not in str(ei.value)
    assert any(c["code"] == "too_few_rows" for c in ei.value.checks)
    # nothing was written to the models folder
    assert not list((state.captures_dir.parent / "models").glob("*/model.pkl"))


def test_state_trains_through_nan_labels_and_reports_them(state):
    _write(state.captures_dir / "gappy.csv", _rows(400, nan_labels=15))
    res = state.train_from_captures(["gappy.csv"], n_estimators=4, role="left",
                                    activate=False)
    assert res["metrics"]["n_rows"] == 385
    assert "nan_rows_dropped" in [c["code"] for c in res["checks"]]
    assert "single_capture" in [c["code"] for c in res["checks"]]


def test_state_trains_left_and_right_files_from_different_times(state):
    _write(state.captures_dir / "left1.csv", _rows(400, role="left", device="fg-L"))
    _write(state.captures_dir / "left2.csv",
           _rows(400, role="left", device="fg-L", t0=5_000_000))
    _write(state.captures_dir / "right1.csv",
           _rows(400, role="right", device="fg-R", t0=900_000_000, base=30))
    res = state.train_from_captures(
        ["left1.csv", "left2.csv", "right1.csv"], n_estimators=4, activate=True)
    assert res["per_hand"] is True
    assert set(res["results"]) == {"left", "right"}
    assert res["results"]["left"]["role"] == "left"
    assert res["results"]["right"]["role"] == "right"
    assert res["results"]["left"]["metrics"]["n_rows"] == 800
    assert res["results"]["right"]["metrics"]["n_rows"] == 400
    assert "trained_each_side" in [c["code"] for c in res["checks"]]
    # both hands are loaded into their own slot
    assert state.engines["left"] is not None and state.engines["right"] is not None
    assert res["active"] is True
    # the models are two different folders
    assert res["results"]["left"]["model_dir"] != res["results"]["right"]["model_dir"]


def test_state_blocks_both_hands_before_fitting_either(state):
    _write(state.captures_dir / "left1.csv", _rows(400, role="left", device="fg-L"))
    _write(state.captures_dir / "right1.csv",
           _rows(8, role="right", device="fg-R", t0=900_000_000))
    with pytest.raises(TrainDataError) as ei:
        state.train_from_captures(["left1.csv", "right1.csv"], n_estimators=4)
    assert str(ei.value).startswith("Right hand:")
    assert state.engines["left"] is None          # the left hand was not trained either


def test_state_maps_schema_mismatch_to_plain_words(state):
    _write(state.captures_dir / "a.csv", _rows(400))
    # a different width: an extra sensor column
    p = state.captures_dir / "b.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER[:3] + ["R0C0", "R0C1"] + ["label_0", "label_1"])
        w.writerow([1, "left", "fg-1", 1, 2, 0.1, 0.2])
    with pytest.raises(TrainDataError) as ei:
        state.train_from_captures(["a.csv", "b.csv"], n_estimators=4, role="left")
    assert "cannot be trained together" in str(ei.value)
    assert "combine_csvs" not in str(ei.value)


def _route(app, path, method):
    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ()):
            return r.endpoint
    raise AssertionError(f"no route {method} {path}")


def test_http_400_for_blocks_with_message_and_checks(tmp_path):
    caps = tmp_path / "captures"
    caps.mkdir()
    app = create_app(udp_port=53841, captures_dir=str(caps))
    _write(caps / "tiny.csv", _rows(8))
    _write(caps / "flat.csv", _rows(400, flat=True))
    _write(caps / "const.csv", _rows(400, const_labels=True))
    _write(caps / "nan.csv", _rows(400, all_nan=True))
    train = _route(app, "/api/train", "POST")
    body_cls = train.__annotations__["body"]
    for name, needle in (("tiny.csv", "at least 150"), ("flat.csv", "not touching the arm"),
                         ("const.csv", "never moved"), ("nan.csv", "no readings")):
        resp = asyncio.run(train(body_cls(captures=[name], role="left", n_estimators=4)))
        assert resp.status_code == 400
        payload = json.loads(resp.body)
        assert needle in payload["detail"]
        assert payload["checks"] and payload["checks"][0]["level"] == "block"
        assert "n_samples" not in resp.body.decode()
        assert "Input y contains NaN" not in resp.body.decode()


def test_http_200_with_checks_for_warnings(tmp_path):
    caps = tmp_path / "captures"
    caps.mkdir()
    app = create_app(udp_port=53842, captures_dir=str(caps))
    _write(caps / "ok.csv", _rows(400, nan_labels=5))
    train = _route(app, "/api/train", "POST")
    body_cls = train.__annotations__["body"]
    res = asyncio.run(train(body_cls(captures=["ok.csv"], role="left",
                                     n_estimators=4, activate=False)))
    assert isinstance(res, dict)
    codes = [c["code"] for c in res["checks"]]
    assert "nan_rows_dropped" in codes and "single_capture" in codes
    assert all(c["level"] == "warn" for c in res["checks"])
    assert "verdict" in res and res["verdict"]["headline"]


# -------------------------------------------------------------------- CLI

def test_cli_train_blocks_with_plain_message(tmp_path):
    from click.testing import CliRunner
    from openmuscle.cli import main
    _write(tmp_path / "tiny.csv", _rows(8))
    res = CliRunner().invoke(main, ["train", str(tmp_path / "tiny.csv"), "--role", "left",
                                    "--trees", "4"])
    assert res.exit_code != 0
    assert "at least 150" in res.output and "n_samples" not in res.output
    assert "Traceback" not in res.output


def test_cli_train_left_and_right_files_say_to_train_each_hand(tmp_path):
    from click.testing import CliRunner
    from openmuscle.cli import main
    _write(tmp_path / "l.csv", _rows(300, role="left"))
    _write(tmp_path / "r.csv", _rows(300, role="right", t0=900_000_000))
    res = CliRunner().invoke(main, ["train", str(tmp_path / "l.csv"),
                                    str(tmp_path / "r.csv"), "--trees", "4"])
    assert res.exit_code != 0
    assert "--role left" in res.output and "--role right" in res.output


def test_cli_models_lists_hand_participant_and_verdict(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from openmuscle.cli import main
    _write(tmp_path / "d.csv", _rows(300, role="left"))
    from openmuscle.ml.training import train_model
    train_model(str(tmp_path / "d.csv"), n_estimators=4, role="left",
                extra_meta={"participant": "p-07"})
    res = CliRunner().invoke(main, ["models"])
    assert res.exit_code == 0, res.output
    assert "left hand" in res.output and "p-07" in res.output
    assert "Not tested" in res.output or "Fair" in res.output or "Weak" in res.output \
        or "Good" in res.output or "Not working" in res.output
