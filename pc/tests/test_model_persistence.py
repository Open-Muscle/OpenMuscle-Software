"""Restart-safety tests (board #0314.1 / #0311.d-e): per-hand models auto-restore
from the registry on server start, and the load-time feature-count guard refuses
models trained on a different sensor layout (the stale-64-feature trap)."""

import json
import pickle
import time

import pytest
from sklearn.dummy import DummyRegressor

from openmuscle.web.inference import InferenceEngine
from openmuscle.web.state import AppState


def make_model_dir(base, name, role, n_features, n_labels=4, order=0):
    """Fabricate a registry entry: data/models/<name>/model.pkl + metadata.json,
    the exact layout ModelRegistry.save produces (role inside metrics)."""
    d = base / "data" / "models" / name
    d.mkdir(parents=True)
    model = DummyRegressor(strategy="constant", constant=[0.5] * n_labels)
    X = [[0.0] * n_features, [1.0] * n_features]
    model.fit(X, [[0.5] * n_labels, [0.5] * n_labels])
    with open(d / "model.pkl", "wb") as f:
        pickle.dump(model, f)
    with open(d / "metadata.json", "w") as f:
        json.dump({"name": name, "created": name.split("_", 1)[-1],
                   "metrics": {"role": role, "n_features": n_features,
                               "n_labels": n_labels, "r2": 0.9}}, f)
    # Keep directory mtimes distinct/ordered like real timestamped runs.
    t = time.time() - 1000 + order
    import os
    os.utime(d, (t, t))
    return d / "model.pkl"


def make_state(tmp_path):
    return AppState(udp_port=53998, captures_dir=str(tmp_path / "caps"))


class TestAutoRestore:
    def test_autoloads_newest_per_role(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 60, order=0)
        newer_left = make_model_dir(tmp_path, "random_forest_20260102_000000", "left", 60, order=1)
        right = make_model_dir(tmp_path, "random_forest_20260102_000001", "right", 60, order=2)
        s = make_state(tmp_path)
        assert s.engines["left"] is not None and s.engines["right"] is not None
        # Newest left wins (registry sorts by dir name = timestamp). The registry
        # hands back cwd-relative paths; compare resolved.
        from pathlib import Path
        assert Path(s.engines["left"].model_path).resolve() == newer_left.resolve()
        assert Path(s.engines["right"].model_path).resolve() == right.resolve()
        # Restart-safety: restored models mean inference comes back enabled.
        assert s.inference_enabled is True

    def test_untagged_models_never_autoload(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # A stale pre-role model (no role in metrics) -- e.g. the 64-feature one.
        make_model_dir(tmp_path, "random_forest_20260101_000000", None, 64)
        s = make_state(tmp_path)
        assert s.engines["left"] is None and s.engines["right"] is None
        assert s.inference_enabled is False

    def test_no_models_dir_is_fine(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        s = make_state(tmp_path)
        assert s.engines["left"] is None and s.engines["right"] is None


class TestFeatureGuard:
    def test_engine_refuses_mismatched_features(self, tmp_path):
        path = make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 64)
        with pytest.raises(ValueError) as ei:
            InferenceEngine(str(path), expected_features=60)
        msg = str(ei.value)
        assert "64" in msg and "60" in msg and "different sensor layout" in msg

    def test_engine_loads_when_matching_or_unknown(self, tmp_path):
        path = make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 60)
        assert InferenceEngine(str(path), expected_features=60).n_features == 60
        assert InferenceEngine(str(path)).n_features == 60   # no band seen yet

    def test_set_model_role_guards_against_live_band(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stale = make_model_dir(tmp_path, "random_forest_20260101_000000", None, 64)
        s = make_state(tmp_path)
        # Simulate a live 15x4 V4 band tagged left.
        from openmuscle.web.state import DeviceInfo
        d = DeviceInfo(device_id="fg-1", device_type="flexgrid")
        d.rows, d.cols = 4, 15
        s.devices["fg-1"] = d
        s._role_by_device = {"fg-1": "left"}
        with pytest.raises(ValueError):
            s.set_model_role("left", str(stale))
        assert s.engines["left"] is None
