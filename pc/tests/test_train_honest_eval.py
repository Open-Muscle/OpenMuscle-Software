"""Honest held-out evaluation in the trainer (hold out whole captures or a
chronological tail, never a shuffled row split) and scoring in finger-curl space.

All data is synthetic and tiny so the file runs in a few seconds.
"""

import csv
import json
import math
import random

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score

from openmuscle.data.converter import combine_csvs, capture_labels
from openmuscle.hand_angles import canonical_labels_from_flat
from openmuscle.ml import evaluate as ev
from openmuscle.ml.training import train_model
from openmuscle.simulate.quest_hand import (
    finger_curls, flexgrid_matrix, hand_pose)
from openmuscle.web.state import AppState, _flatten_quest_joint

TREES = 12
IDENTITY = [0.0, 0.0, 0.0, 1.0]


def _write(path, rows, n_feat, n_label):
    """rows: list of (feats, labels). Schema-v2, single role, 40 ms apart."""
    header = (["ts_hub_ms", "role", "device_id"]
              + [f"R0C{c}" for c in range(n_feat)]
              + [f"label_{i}" for i in range(n_label)])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i, (feats, labels) in enumerate(rows):
            w.writerow([1_000_000 + i * 40, "left", "fg-left"]
                       + list(feats) + list(labels))


def _ramp_rows(n, seed=0):
    """Autocorrelated capture: feature 0 is a time ramp, label is a slow sine of
    time. A shuffled split interpolates it perfectly; a chronological tail needs
    extrapolation and cannot."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        feats = [i / n] + [rng.random() for _ in range(3)]
        labels = [math.sin(i / 40.0), math.cos(i / 55.0)]
        rows.append((feats, labels))
    return rows


def _pose_flat(positions):
    out = []
    for p in positions:
        out.extend(_flatten_quest_joint(p, IDENTITY))
    return out


def _sim_rows(n, noise_band=False, seed=7):
    """Combo-sim shaped rows: 64-cell flexgrid + 175-wide hand pose, 12.5 Hz."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        t = i * 0.08
        positions, _q = hand_pose(t)
        if noise_band:
            feats = [rng.random() * 4000 for _ in range(64)]
        else:
            m = flexgrid_matrix(finger_curls(t), rng)
            feats = [m[c][r] for r in range(4) for c in range(16)]
        rows.append((feats, _pose_flat(positions)))
    return rows


def _write_sim(path, rows):
    header = (["ts_hub_ms", "role", "device_id"]
              + [f"R{r}C{c}" for r in range(4) for c in range(16)]
              + [f"label_{i}" for i in range(175)])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i, (feats, labels) in enumerate(rows):
            w.writerow([1_000_000 + i * 80, "left", "fg-sim"] + feats + labels)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)        # ModelRegistry writes data/models under CWD


# ----------------------------------------------------------------- split plan

def test_tail_split_has_purge_gap_and_is_chronological():
    plan = ev.plan_holdout(400, times_ms=[i * 40 for i in range(400)],
                           test_split=0.2)
    assert plan["method"] == "held_out_tail"
    assert plan["eval_idx"].tolist() == list(range(320, 400))
    assert plan["purge_rows"] == 25                    # 1 s at 25 Hz
    assert plan["train_idx"].tolist() == list(range(0, 295))


def test_capture_split_holds_out_last_whole_captures():
    caps = ["a"] * 50 + ["b"] * 50 + ["c"] * 50 + ["d"] * 50 + ["e"] * 50
    plan = ev.plan_holdout(250, captures=caps, test_split=0.2)
    assert plan["method"] == "held_out_capture"
    assert plan["eval_captures"] == ["e"]
    assert plan["purge_rows"] == 0
    assert set(plan["eval_idx"]) == set(range(200, 250))
    # user-chosen hold-out wins
    plan = ev.plan_holdout(250, captures=caps, holdout_captures=["b", "c"])
    assert plan["eval_captures"] == ["b", "c"]
    assert not set(plan["train_idx"]) & set(plan["eval_idx"])


def test_two_captures_hold_out_exactly_the_second():
    plan = ev.plan_holdout(100, captures=["x"] * 60 + ["y"] * 40)
    assert plan["eval_captures"] == ["y"]
    assert len(plan["train_idx"]) == 60


def test_too_few_rows_reports_none():
    plan = ev.plan_holdout(9, test_split=0.2)
    assert plan["method"] == "none"
    assert len(plan["eval_idx"]) == 0


def test_tiny_capture_hold_out_falls_back_to_tail():
    # the later capture is too small to score on: fall back to a tail
    plan = ev.plan_holdout(62, captures=["a"] * 60 + ["b"] * 2)
    assert plan["method"] == "held_out_tail"


# ------------------------------------------------------- honest vs shuffled

def test_held_out_r2_is_below_shuffled_and_matches_manual_chronological(tmp_path):
    rows = _ramp_rows(400)
    csv_path = tmp_path / "ramp.csv"
    _write(csv_path, rows, 4, 2)

    model, m = train_model(str(csv_path), n_estimators=TREES,
                           output=str(tmp_path / "m.pkl"))

    assert m["eval_method"] == "held_out_tail"
    assert m["n_eval_rows"] == 80 and m["purge_rows"] == 25
    assert m["r2"] < m["r2_random_split"]
    assert m["r2_random_split"] > 0.9            # the optimistic number
    assert m["r2"] < 0.5                          # the honest one
    assert "optimistic" in m["random_split_note"]

    # independent chronological computation
    df = pd.read_csv(csv_path)       # same float parse as the trainer
    X = df.filter(like="R0C").to_numpy()
    y = df.filter(like="label_").to_numpy()
    ref = RandomForestRegressor(n_estimators=TREES, random_state=42)
    ref.fit(X[:295], y[:295])
    manual = r2_score(y[320:], ref.predict(X[320:]))
    assert abs(m["r2"] - manual) < 1e-6
    manual_mse = float(np.mean((y[320:] - ref.predict(X[320:])) ** 2))
    assert abs(m["mse"] - manual_mse) < 1e-6


def test_final_model_is_fit_on_all_rows_with_all_cores(tmp_path):
    csv_path = tmp_path / "ramp.csv"
    _write(csv_path, _ramp_rows(200), 4, 2)
    model, m = train_model(str(csv_path), n_estimators=TREES,
                           output=str(tmp_path / "m.pkl"))
    assert model.n_jobs == -1
    assert m["n_rows"] == 200 and m["n_train"] == 200
    # bootstrap draws n_rows samples per tree: the tree root saw all 200 slots
    assert model.estimators_[0].tree_.weighted_n_node_samples[0] == 200


def test_metrics_are_json_serialisable(tmp_path):
    csv_path = tmp_path / "ramp.csv"
    _write(csv_path, _ramp_rows(200), 4, 2)
    _model, m = train_model(str(csv_path), n_estimators=TREES,
                            output=str(tmp_path / "m.pkl"))
    json.dumps(m)


def test_too_small_capture_has_no_number_but_still_trains(tmp_path):
    csv_path = tmp_path / "tiny.csv"
    _write(csv_path, _ramp_rows(8), 4, 2)
    _model, m = train_model(str(csv_path), n_estimators=4,
                            output=str(tmp_path / "m.pkl"))
    assert m["eval_method"] == "none"
    assert m["r2"] is None and m["curl_r2"] is None and m["n_eval_rows"] == 0
    assert m["n_rows"] == 8


# --------------------------------------------------------- whole-capture holdout

def test_combine_csvs_capture_column(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    for p in (a, b):
        _write(p, _ramp_rows(5), 4, 2)
    out = tmp_path / "out.csv"
    assert combine_csvs([str(a), str(b)], str(out)) == 10
    assert "_capture" not in out.read_text().splitlines()[0]
    assert combine_csvs([str(a), str(b), str(a)], str(out),
                        capture_column="_capture") == 15
    lines = out.read_text().splitlines()
    assert lines[0].endswith(",_capture")
    assert [ln.split(",")[-1] for ln in lines[1:]] == (
        ["a.csv"] * 5 + ["b.csv"] * 5 + ["a.csv#2"] * 5)
    assert capture_labels(["x/a.csv", "y/a.csv"]) == ["a.csv", "a.csv#2"]


def test_multi_capture_holds_out_whole_captures(tmp_path):
    paths = []
    for k in range(4):
        p = tmp_path / f"take{k}.csv"
        _write(p, _ramp_rows(120, seed=k), 4, 2)
        paths.append(str(p))
    comb = tmp_path / "comb.csv"
    combine_csvs(paths, str(comb), capture_column="_capture")

    _model, m = train_model(str(comb), n_estimators=TREES,
                            output=str(tmp_path / "m.pkl"))
    assert m["eval_method"] == "held_out_capture"
    assert m["eval_captures"] == ["take3.csv"]          # round(4 * 0.2) = 1, the last
    assert m["n_eval_rows"] == 120 and m["purge_rows"] == 0

    _model, m = train_model(str(comb), n_estimators=TREES,
                            output=str(tmp_path / "m2.pkl"),
                            holdout_captures=["take0.csv"])
    assert m["eval_captures"] == ["take0.csv"]


def test_rest_take_as_last_capture_gets_a_truthful_verdict(tmp_path):
    from openmuscle.ml.verdict import describe_result
    paths = []
    for k in range(2):
        p = tmp_path / f"take{k}.csv"
        rows = _ramp_rows(120, seed=k)
        if k == 1:                                   # last take is a rest take
            rows = [(f, [0.5, 0.5]) for f, _l in rows]
        _write(p, rows, 4, 2)
        paths.append(str(p))
    comb = tmp_path / "comb.csv"
    combine_csvs(paths, str(comb), capture_column="_capture")
    _model, m = train_model(str(comb), n_estimators=TREES,
                            output=str(tmp_path / "m.pkl"))
    assert m["eval_method"] == "held_out_capture"
    assert m["curl_r2"] is None
    v = describe_result(m)
    assert v["grade"] == "untested" and v["show_live_ok"] is False
    assert "before held-out testing existed" not in v["detail"]
    assert "no finger movement" in v["detail"]


def test_train_from_captures_orders_by_created_ms_and_reports_eval_captures(tmp_path):
    caps = tmp_path / "captures"
    caps.mkdir()
    s = AppState(udp_port=53890, captures_dir=str(caps), enable_discovery=False)
    for k, name in enumerate(["newest.csv", "oldest.csv", "middle.csv"]):
        _write(caps / name, _ramp_rows(100, seed=k), 4, 2)
    s.write_capture_meta("oldest.csv", {"created_ms": 1000})
    s.write_capture_meta("middle.csv", {"created_ms": 2000})
    s.write_capture_meta("newest.csv", {"created_ms": 3000})

    # selection order is deliberately NOT chronological
    res = s.train_from_captures(["newest.csv", "oldest.csv", "middle.csv"],
                                n_estimators=TREES, activate=False)
    m = res["metrics"]
    assert m["eval_method"] == "held_out_capture"
    assert m["eval_captures"] == ["newest.csv"]
    assert m["n_rows"] == 300
    assert res["verdict"]["headline"]


def test_single_capture_through_state_is_a_tail(tmp_path):
    caps = tmp_path / "captures"
    caps.mkdir()
    s = AppState(udp_port=53891, captures_dir=str(caps), enable_discovery=False)
    _write(caps / "solo.csv", _ramp_rows(200), 4, 2)
    res = s.train_from_captures(["solo.csv"], n_estimators=TREES, activate=False)
    assert res["metrics"]["eval_method"] == "held_out_tail"
    assert res["verdict"]["grade"] != "good"        # tail cap


# ------------------------------------------------------- curl-space scoring

_FLEX = ["lbl_flex_thumb", "lbl_flex_index", "lbl_flex_middle",
         "lbl_flex_ring", "lbl_flex_pinky"]


def _manual_curl_r2(csv_path, n_estimators, n_eval, purge):
    df = pd.read_csv(csv_path)       # same float parse as the trainer
    X = df.filter(regex=r"^R\dC\d+$").to_numpy()
    y = df.filter(like="label_").to_numpy()
    n = len(df)
    n_train = n - n_eval - purge
    ref = RandomForestRegressor(n_estimators=n_estimators, random_state=42)
    ref.fit(X[:n_train], y[:n_train])
    pred = ref.predict(X[n - n_eval:])
    tc = np.array([[canonical_labels_from_flat(r.tolist())[k] for k in _FLEX]
                   for r in y[n - n_eval:]])
    pc = np.array([[canonical_labels_from_flat(r.tolist())[k] for k in _FLEX]
                   for r in pred])
    return [r2_score(tc[:, j], pc[:, j]) for j in range(5)]


def test_combo_sim_scores_five_fingers_in_curl_space(tmp_path):
    rows = _sim_rows(750)                      # 60 s at 12.5 Hz
    csv_path = tmp_path / "combo.csv"
    _write_sim(csv_path, rows)
    _model, m = train_model(str(csv_path), n_estimators=20,
                            output=str(tmp_path / "m.pkl"))

    assert m["score_space"] == "curl"
    assert [f["finger"] for f in m["per_finger"]] == [
        "thumb", "index", "middle", "ring", "pinky"]
    assert m["eval_method"] == "held_out_tail"
    assert m["n_constant_labels"] >= 100         # identity rotations never change
    manual = _manual_curl_r2(csv_path, 20, m["n_eval_rows"], m["purge_rows"])
    assert abs(m["curl_r2"] - float(np.mean(manual))) < 0.03
    for f, ref in zip(m["per_finger"], manual):
        assert abs(f["r2"] - ref) < 0.03
    assert 0.0 <= m["curl_mae"] <= 1.0


def test_webcam_shaped_noise_does_not_score_well(tmp_path):
    # 100 label columns (rotations) never change, the band is pure noise.
    rows = _sim_rows(500, noise_band=True)
    csv_path = tmp_path / "webcam_noise.csv"
    _write_sim(csv_path, rows)
    _model, m = train_model(str(csv_path), n_estimators=20,
                            output=str(tmp_path / "m.pkl"))
    assert m["n_constant_labels"] >= 100
    assert m["curl_r2"] is not None and m["curl_r2"] <= 0.05
    # the legacy average no longer counts the constant columns as perfect
    assert m["r2"] is None or m["r2"] < 0.3


def test_lask5_four_labels_score_four_fingers(tmp_path):
    rng = random.Random(3)
    rows = []
    for i in range(400):
        curls = [0.5 + 0.5 * math.sin(i / (8.0 + 3 * k) + k) for k in range(4)]
        feats = [curls[k % 4] * 100 + rng.gauss(0, 1) for k in range(8)]
        rows.append((feats, curls))
    csv_path = tmp_path / "lask5.csv"
    _write(csv_path, rows, 8, 4)
    _model, m = train_model(str(csv_path), n_estimators=TREES,
                            output=str(tmp_path / "m.pkl"))
    assert m["score_space"] == "piston"
    assert [f["finger"] for f in m["per_finger"]] == [
        "index", "middle", "ring", "pinky"]
    assert m["curl_r2"] > 0.5


def test_constant_label_columns_are_ignored_and_counted(tmp_path):
    rng = random.Random(5)
    rows = []
    for i in range(300):
        v = math.sin(i / 20.0)
        rows.append(([v + rng.gauss(0, .02) for _ in range(4)], [v, 3.0, 3.0, 3.0]))
    csv_path = tmp_path / "const.csv"
    _write(csv_path, rows, 4, 4)
    _model, m = train_model(str(csv_path), n_estimators=TREES,
                            output=str(tmp_path / "m.pkl"))
    assert m["n_constant_labels"] == 3
    df = pd.read_csv(csv_path)
    X = df.filter(like="R0C").to_numpy()
    y = df.filter(like="label_").to_numpy()
    plan = ev.plan_holdout(300, times_ms=[i * 40 for i in range(300)])
    tr, te = plan["train_idx"], plan["eval_idx"]
    ref = RandomForestRegressor(n_estimators=TREES, random_state=42).fit(X[tr], y[tr])
    manual = r2_score(y[te, 0], ref.predict(X[te])[:, 0])
    assert abs(m["r2"] - manual) < 1e-6        # only the varying column counts


def test_score_heldout_reads_canonical_columns_when_present():
    rng = np.random.default_rng(1)
    truth = rng.random((60, 5))
    y_all = rng.normal(size=(60, 175))
    pred = rng.normal(size=(60, 175))
    out = ev.score_heldout(y_all, pred, [f"label_{i}" for i in range(175)], y_all,
                           canon_true=pd.DataFrame(truth, columns=ev._FLEX_KEYS))
    # the prediction is a degenerate pose; the truth comes straight from the columns
    assert [f["finger"] for f in out["per_finger"]][0] == "thumb"
    assert all(f["r2"] is not None for f in out["per_finger"])


# ------------------------------------------------- bilateral + role bookkeeping

def _write_two_hand(path, n, seed):
    rng = random.Random(seed)
    header = ["ts_hub_ms", "role", "device_id", "R0C0", "R0C1", "label_0", "label_1"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i in range(n):
            v = math.sin(i / 7.0)
            for role in ("left", "right"):
                w.writerow([5000 + i * 40, role, f"fg-{role}",
                            v + rng.gauss(0, .05), rng.random(), v, 1.0 - v])


def test_bilateral_captures_pair_within_each_capture_and_hold_out_the_last(tmp_path):
    paths = []
    for k in range(3):
        p = tmp_path / f"two{k}.csv"
        _write_two_hand(p, 60, k)          # same hub clock in every capture
        paths.append(str(p))
    comb = tmp_path / "comb.csv"
    combine_csvs(paths, str(comb), capture_column="_capture")

    _model, m = train_model(str(comb), n_estimators=TREES, output=str(tmp_path / "b.pkl"))
    assert m["n_features"] == 4            # Left||Right pivot, not a pooled merge
    assert m["n_rows"] == 180              # nothing lost to cross-capture pairing
    assert m["eval_method"] == "held_out_capture"
    assert m["eval_captures"] == ["two2.csv"]

    # per-hand training of the same file keeps the same bookkeeping
    _model, m = train_model(str(comb), n_estimators=TREES, role="right",
                            output=str(tmp_path / "r.pkl"))
    assert m["n_rows"] == 180 and m["eval_captures"] == ["two2.csv"]
