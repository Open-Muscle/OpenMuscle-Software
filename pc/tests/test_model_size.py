"""Model size: a model trained from camera hand labels used to keep all 175 label
columns in every node of every tree (hundreds of MB). The trainer now learns the
five finger curls by default, caps the forest, and the Models list shows each
model's size and can delete it.

All data is synthetic and small so the file runs in a few seconds.
"""

import csv
import json
import math
import os
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from openmuscle.hand_angles import canonical_labels_from_flat
from openmuscle.ml import evaluate as ev
from openmuscle.ml import training
from openmuscle.ml.registry import ModelRegistry
from openmuscle.ml.training import curl_targets, plan_forest, train_model
from openmuscle.web.app import create_app
from openmuscle.web.state import ModelInUseError

from tests.asgi_helper import call_json
from tests.test_train_honest_eval import _sim_rows

pytestmark = pytest.mark.usefixtures("tiny_training_ok")

FLEX = list(ev._FLEX_KEYS)


def _write_hand_csv(path, n, canonical=True, seed=7):
    """Webcam-shaped capture: 64 cells, 175 hand columns, optional lbl_flex_*."""
    rows = _sim_rows(n, seed=seed)
    header = (["ts_hub_ms", "role", "device_id"]
              + [f"R{r}C{c}" for r in range(4) for c in range(16)]
              + [f"label_{i}" for i in range(175)]
              + (FLEX if canonical else []))
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i, (feats, labels) in enumerate(rows):
            extra = []
            if canonical:
                can = canonical_labels_from_flat(labels)
                extra = [can[k] for k in FLEX]
            w.writerow([1_000_000 + i * 80, "left", "fg-sim"] + feats + labels + extra)


# ------------------------------------------------------------- column choice

def _frame(n=6, canonical=True):
    rows = _sim_rows(n)
    y = pd.DataFrame([r[1] for r in rows], columns=[f"label_{i}" for i in range(175)])
    X = pd.DataFrame([r[0] for r in rows])
    extras = pd.DataFrame(index=y.index)
    for k in FLEX:
        extras[k] = ([canonical_labels_from_flat(r[1])[k] for r in rows]
                     if canonical else np.nan)
    return X, y, extras


def test_curl_targets_uses_the_recorded_curl_columns():
    X, y, extras = _frame()
    extras[FLEX] = np.linspace(0.1, 0.9, 6).reshape(6, 1) * np.ones((1, 5))
    X2, y2, ex2 = curl_targets(X, y, extras)
    assert list(y2.columns) == FLEX and y2.shape == (6, 5)
    assert np.allclose(y2.to_numpy(), extras[FLEX].to_numpy())
    assert len(X2) == len(ex2) == 6


def test_curl_targets_derives_the_curls_when_the_columns_are_missing():
    X, y, extras = _frame(canonical=False)
    _X2, y2, _ex = curl_targets(X, y, extras)
    want = np.array([[canonical_labels_from_flat(r.tolist())[k] for k in FLEX]
                     for r in y.to_numpy()])
    assert np.allclose(y2.to_numpy(), want)


def test_curl_targets_fills_only_the_gaps_and_drops_unusable_rows():
    X, y, extras = _frame()
    extras.loc[2, "lbl_flex_index"] = np.nan          # one cell: worked out from joints
    y.iloc[4, :] = np.nan                              # no joints at all...
    extras.loc[4, "lbl_flex_pinky"] = np.nan           # ...and no recorded curl
    X2, y2, ex2 = curl_targets(X, y, extras)
    assert len(y2) == len(X2) == len(ex2) == 5         # row 4 dropped from all three
    assert np.isfinite(y2.to_numpy()).all()
    want = canonical_labels_from_flat(_sim_rows(6)[2][1])["lbl_flex_index"]
    assert y2.iloc[2]["lbl_flex_index"] == pytest.approx(want)
    assert X2.index.tolist() == list(range(5))


def test_curl_targets_refuses_a_recording_with_no_usable_hand():
    X, y, extras = _frame(canonical=False)
    y[:] = np.nan
    with pytest.raises(ValueError):
        curl_targets(X, y, extras)


def test_score_heldout_reads_curl_labels_directly():
    rng = np.random.default_rng(2)
    truth = rng.random((50, 5))
    pred = np.clip(truth + rng.normal(0, 0.05, truth.shape), 0, 1)
    out = ev.score_heldout(truth, pred, FLEX, truth)
    assert out["score_space"] == "curl"
    assert [f["finger"] for f in out["per_finger"]] == list(ev.POSE_FINGERS)
    assert out["curl_r2"] > 0.9


# -------------------------------------------------------------------- sizing

def test_plan_forest_clamps_the_tree_count():
    assert plan_forest(1000, 5, 100000)["n_estimators"] == training.MAX_TREES
    assert plan_forest(1000, 5, 0)["n_estimators"] == training.DEFAULT_TREES
    assert plan_forest(1000, 5, 7)["n_estimators"] == 7


def test_plan_forest_keeps_the_minimum_leaf_for_a_short_recording():
    p = plan_forest(2500, 5, 100)
    assert p["min_samples_leaf"] == training.MIN_SAMPLES_LEAF
    assert p["est_mb"] <= p["budget_mb"]


def test_plan_forest_grows_the_leaf_so_a_long_recording_stays_in_budget():
    short, long_ = plan_forest(7500, 5, 100), plan_forest(200000, 5, 100)
    assert long_["min_samples_leaf"] > short["min_samples_leaf"]
    assert long_["est_mb"] <= long_["budget_mb"] * 1.01
    # the whole-hand model is allowed more room, and needs it
    assert plan_forest(2500, 175, 100, "full")["budget_mb"] > long_["budget_mb"]


def test_two_band_training_budget_is_well_under_100_mb():
    one_band = plan_forest(10 ** 6, 5, 100)
    assert 2 * one_band["budget_mb"] < 100


def test_trained_curl_model_is_small_and_predicts_five_curls(tmp_path):
    csv_path = tmp_path / "hand.csv"
    _write_hand_csv(csv_path, 750)
    out = tmp_path / "curls" / "model.pkl"
    import pickle
    model, m = train_model(str(csv_path), n_estimators=100, output=str(out))
    assert m["label_set"] == "curls" and m["n_labels"] == 5
    assert m["label_columns"] == FLEX
    assert m["score_space"] == "curl" and m["curl_r2"] is not None
    assert [f["finger"] for f in m["per_finger"]] == list(ev.POSE_FINGERS)
    assert m["n_constant_labels"] >= 100      # counted on what was recorded
    assert m["n_estimators"] <= training.MAX_TREES and m["leaf_min_rows"] >= 3
    with open(out, "rb") as f:
        loaded = pickle.load(f)
    assert loaded.predict(pd.DataFrame(np.zeros((1, 64)),
                                       columns=list(loaded.feature_names_in_))).shape == (1, 5)
    size = os.path.getsize(out)
    assert size < m["est_model_mb"] * 1e6 * 1.5       # the estimate is honest
    assert size < training.SIZE_BUDGET_MB["curls"] * 1e6


def test_curl_model_is_much_smaller_than_the_full_hand_and_just_as_good(tmp_path):
    csv_path = tmp_path / "hand.csv"
    _write_hand_csv(csv_path, 750)
    small_path, full_path = tmp_path / "s" / "model.pkl", tmp_path / "f" / "model.pkl"
    _m1, small = train_model(str(csv_path), n_estimators=30, output=str(small_path))
    _m2, full = train_model(str(csv_path), n_estimators=30, labels="full",
                            output=str(full_path))
    assert full["label_set"] == "full" and full["n_labels"] == 175
    assert os.path.getsize(small_path) * 10 < os.path.getsize(full_path)
    assert small["curl_r2"] >= full["curl_r2"] - 0.1     # the score does not drop


def test_curl_model_without_recorded_curl_columns_still_trains(tmp_path):
    csv_path = tmp_path / "old.csv"
    _write_hand_csv(csv_path, 400, canonical=False)
    _model, m = train_model(str(csv_path), n_estimators=10,
                            output=str(tmp_path / "m.pkl"))
    assert m["label_set"] == "curls" and m["curl_r2"] is not None


def test_a_small_budget_raises_the_leaf_and_shrinks_the_model(tmp_path, monkeypatch):
    csv_path = tmp_path / "hand.csv"
    _write_hand_csv(csv_path, 750)
    _a, base = train_model(str(csv_path), n_estimators=60, output=str(tmp_path / "a.pkl"))
    monkeypatch.setitem(training.SIZE_BUDGET_MB, "curls", 0.5)
    _b, capped = train_model(str(csv_path), n_estimators=60, output=str(tmp_path / "b.pkl"))
    assert capped["leaf_min_rows"] > base["leaf_min_rows"]
    assert os.path.getsize(tmp_path / "b.pkl") < os.path.getsize(tmp_path / "a.pkl")
    assert os.path.getsize(tmp_path / "b.pkl") < 0.5e6 * 1.5


def test_labels_other_than_a_hand_are_trained_as_recorded(tmp_path):
    path = tmp_path / "lask.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_hub_ms", "role", "device_id", "R0C0", "R0C1"]
                   + [f"label_{i}" for i in range(4)])
        for i in range(200):
            v = math.sin(i / 9.0)
            w.writerow([1000 + i * 40, "left", "x", v, 1 - v, v, v, 1 - v, v])
    _m, m = train_model(str(path), n_estimators=5, output=str(tmp_path / "m.pkl"))
    assert m["label_set"] == "recorded" and m["n_labels"] == 4
    with pytest.raises(ValueError):
        train_model(str(path), labels="everything", output=str(tmp_path / "x.pkl"))


# ------------------------------------------------------ registry: size, delete

def _make_model(base, name, payload=b"x" * 4096):
    d = Path(base) / "models" / name
    d.mkdir(parents=True)
    (d / "model.pkl").write_bytes(payload)
    (d / "metadata.json").write_text(json.dumps(
        {"name": name, "created": "20261009_143015", "metrics": {"role": "left"}}))
    return d


def test_list_models_reports_the_folder_size(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    d = _make_model(tmp_path, "random_forest_a", b"y" * 10000)
    (m,) = ModelRegistry().list_models()
    assert m["size_bytes"] == 10000 + (d / "metadata.json").stat().st_size


def test_registry_delete_removes_only_that_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    a = _make_model(tmp_path, "random_forest_a")
    b = _make_model(tmp_path, "random_forest_b")
    freed = ModelRegistry().delete("random_forest_a")
    assert freed >= 4096 and not a.exists() and b.exists()
    with pytest.raises(FileNotFoundError):
        ModelRegistry().delete("random_forest_a")


@pytest.mark.parametrize("bad", ["", ".", "..", "../captures", "a/b", "a\\b",
                                 "C:\\Windows", "models", "active.json"])
def test_registry_refuses_names_that_are_not_a_model_folder(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    _make_model(tmp_path, "random_forest_a")
    (tmp_path / "models" / "active.json").write_text("{}")
    (tmp_path / "captures").mkdir(exist_ok=True)
    (tmp_path / "captures" / "keep.csv").write_text("a")
    with pytest.raises(FileNotFoundError):
        ModelRegistry().delete(bad)
    assert (tmp_path / "captures" / "keep.csv").exists()
    assert (tmp_path / "models" / "active.json").exists()


# --------------------------------------------------------------- delete route

def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    return create_app(udp_port=53871, captures_dir=str(tmp_path / "caps"))


def test_delete_route_removes_the_model_and_the_list_drops_it(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    d = _make_model(tmp_path, "random_forest_gone")
    keep = _make_model(tmp_path, "random_forest_keep")
    status, rows = call_json(app, "GET", "/api/models")
    assert status == 200 and {r["dir"] for r in rows} == {d.name, keep.name}
    assert all(r["size_bytes"] > 4096 for r in rows)
    status, body = call_json(app, "DELETE", "/api/models/random_forest_gone")
    assert status == 200 and body["deleted"] == "random_forest_gone"
    assert body["freed_bytes"] > 4096
    assert not d.exists() and keep.exists()
    status, rows = call_json(app, "GET", "/api/models")
    assert [r["dir"] for r in rows] == [keep.name]


def test_delete_route_404_for_unknown_and_for_paths(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    _make_model(tmp_path, "random_forest_a")
    (tmp_path / "captures").mkdir()
    (tmp_path / "captures" / "keep.csv").write_text("a")
    for name in ("nothing_here", "..", "%2e%2e", "..%2fcaptures", "a%5Cb"):
        status, _ = call_json(app, "DELETE", "/api/models/" + name)
        assert status in (404, 405), name
    assert (tmp_path / "captures" / "keep.csv").exists()
    assert (tmp_path / "models" / "random_forest_a").exists()


def test_delete_route_refuses_a_model_that_is_in_use(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    d = _make_model(tmp_path, "random_forest_live")
    state = app.state.app_state
    state.engine = types.SimpleNamespace(model_path=d / "model.pkl", name=d.name)
    status, body = call_json(app, "DELETE", "/api/models/random_forest_live")
    assert status == 409 and "in use" in body["detail"]
    assert d.exists()
    with pytest.raises(ModelInUseError):
        state.delete_model("random_forest_live")
    # a per-hand slot counts too
    state.engine = None
    state.engines["right"] = types.SimpleNamespace(model_path=d / "model.pkl", name=d.name)
    status, body = call_json(app, "DELETE", "/api/models/random_forest_live")
    assert status == 409 and "right" in body["detail"]
    # freed up: now it deletes
    state.engines["right"] = None
    status, _ = call_json(app, "DELETE", "/api/models/random_forest_live")
    assert status == 200 and not d.exists()


def test_train_route_takes_a_labels_option(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    caps = tmp_path / "caps"
    caps.mkdir(exist_ok=True)
    _write_hand_csv(caps / "take0.csv", 500)
    status, out = call_json(app, "POST", "/api/train",
                            body={"captures": ["take0.csv"], "role": "left",
                                  "n_estimators": 10, "activate": False})
    assert status == 200, out
    assert out["metrics"]["label_set"] == "curls" and out["metrics"]["n_labels"] == 5
    status, out = call_json(app, "POST", "/api/train",
                            body={"captures": ["take0.csv"], "role": "left",
                                  "n_estimators": 10, "activate": False,
                                  "labels": "bogus"})
    assert status == 400


def test_state_trains_curls_loads_them_and_lists_the_size(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    from openmuscle.web.state import AppState
    state = AppState(udp_port=53872, captures_dir=str(tmp_path / "caps"))
    _write_hand_csv(state.captures_dir / "take0.csv", 500)
    out = state.train_from_captures(["take0.csv"], n_estimators=10, role="left")
    assert out["active"] and out["activate_error"] is None
    assert out["metrics"]["label_set"] == "curls"
    assert out["verdict"]["grade"] != "untested"
    eng = state.engines["left"]
    matrix = [[100] * 4 for _ in range(16)]            # [cols][rows], 64 cells
    pred = eng.predict(matrix)
    assert pred is not None and len(pred) == 5
    assert all(-0.2 <= v <= 1.2 for v in pred)

    (m,) = state.list_models()
    assert m["size_bytes"] > 0 and m["size_bytes"] < 5e6
    # in use: refused; after loading nothing else it still is
    with pytest.raises(ModelInUseError):
        state.delete_model(m["dir"])
    state.engines["left"] = None
    assert state.delete_model(m["dir"])["freed_bytes"] == m["size_bytes"]
    assert state.list_models() == []


def test_prediction_export_understands_a_curls_model(tmp_path):
    from openmuscle.ml.export import export_predictions
    csv_path = tmp_path / "hand.csv"
    _write_hand_csv(csv_path, 300)
    out = tmp_path / "m" / "model.pkl"
    train_model(str(csv_path), n_estimators=8, output=str(out))
    meta = export_predictions(str(out), str(csv_path), str(tmp_path / "pred.json"))
    assert meta["label_columns"] == FLEX
    data = json.loads((tmp_path / "pred.json").read_text())
    assert set(data["predictions"]) == set(FLEX)
    assert len(data["labels"]["lbl_flex_index"]) == len(data["predictions"]["lbl_flex_index"])


def test_a_hand_with_no_usable_joints_falls_back_to_the_recorded_columns(tmp_path):
    # placeholder joints (every joint at the same point) have no bones to bend
    path = tmp_path / "flat.csv"
    header = (["ts_hub_ms", "role", "device_id"] + [f"R0C{c}" for c in range(4)]
              + [f"label_{i}" for i in range(175)])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i in range(120):
            w.writerow([1000 + i * 40, "left", "x"] + [i % 7, i % 5, i % 3, i % 2]
                       + [0.0] * 175)
    _model, m = train_model(str(path), n_estimators=5, output=str(tmp_path / "m.pkl"))
    assert m["label_set"] == "full" and m["n_labels"] == 175
