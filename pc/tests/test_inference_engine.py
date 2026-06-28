"""Tests for the web InferenceEngine, esp. the feature-name handling.

A model fit on a named DataFrame remembers feature_names_in_; predicting with a
bare array makes sklearn warn "X does not have valid feature names" EVERY frame.
The engine feeds a names-matched DataFrame instead -- these tests pin that the
warning is gone and the prediction is unchanged.
"""

import json
import pickle
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

from openmuscle.web.inference import InferenceEngine

_COLS = ["R0C0", "R0C1", "R1C0", "R1C1"]      # 2x2 grid, row-major
_MATRIX = [[0.1, 0.3], [0.2, 0.4]]            # [cols][rows] -> 4 features


def _make_model(tmp_path, named=True, n_labels=3, subdir="m"):
    rng = np.random.RandomState(0)
    X = pd.DataFrame(rng.rand(20, 4), columns=_COLS)
    cols = [f"label_{i}" for i in range(n_labels)]
    y = pd.DataFrame(rng.rand(20, n_labels), columns=cols)
    model = RandomForestRegressor(n_estimators=5, random_state=0)
    model.fit(X if named else X.values, y)     # named -> feature_names_in_ set
    d = tmp_path / subdir
    d.mkdir()
    with open(d / "model.pkl", "wb") as f:
        pickle.dump(model, f)
    (d / "metadata.json").write_text(
        json.dumps({"metrics": {"n_features": 4, "n_labels": n_labels}}))
    return str(d / "model.pkl"), model


def _feature_name_warnings(rec):
    return [w for w in rec if "feature names" in str(w.message)]


def test_named_model_predicts_without_warning(tmp_path):
    path, _ = _make_model(tmp_path, named=True)
    eng = InferenceEngine(path)
    assert eng.feature_names is not None
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        pred = eng.predict(_MATRIX)
    assert _feature_name_warnings(rec) == []     # the noisy warning is gone
    assert pred is not None and len(pred) == 3
    assert eng.last_error is None


def test_prediction_matches_model_output(tmp_path):
    path, model = _make_model(tmp_path, named=True)
    eng = InferenceEngine(path)
    flat = [_MATRIX[c][r] for r in range(2) for c in range(2)]
    direct = list(model.predict(pd.DataFrame([flat], columns=eng.feature_names))[0])
    got = eng.predict(_MATRIX)
    assert got == [float(v) for v in direct]     # fix did not change the result


def test_unnamed_model_uses_bare_list_no_warning(tmp_path):
    # Fit on a numpy array -> no feature_names_in_ -> a bare list is correct.
    path, _ = _make_model(tmp_path, named=False)
    eng = InferenceEngine(path)
    assert eng.feature_names is None
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        pred = eng.predict(_MATRIX)
    assert _feature_name_warnings(rec) == []
    assert pred is not None


def test_shape_mismatch_returns_none(tmp_path):
    path, _ = _make_model(tmp_path)
    eng = InferenceEngine(path)
    # 3 cols x 2 rows = 6 features, but the model expects 4.
    assert eng.predict([[1, 2], [3, 4], [5, 6]]) is None
    assert "expects 4" in (eng.last_error or "")


def test_per_band_inference_in_snapshot(tmp_path):
    # Two-hand: each FlexGrid band is run through the model independently and
    # its prediction is exposed under inference.by_device[device_id] so the VR
    # client can draw a ghost hand per real hand.
    import time as _t
    from openmuscle.web.state import AppState
    from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION

    path, _ = _make_model(tmp_path, named=True)
    s = AppState(udp_port=53888, captures_dir=str(tmp_path),
                 model_path=path, enable_discovery=False)
    assert s.engine is not None and s.inference_enabled

    def fg(did):
        return OpenMusclePacket(
            version=CURRENT_VERSION, device_type="flexgrid", device_id=did,
            timestamp_ms=0, data={"matrix": [[0.1, 0.3], [0.2, 0.4]]},
            receive_time=_t.time())

    s._handle_packet(fg("fg-left"))
    s._handle_packet(fg("fg-right"))
    inf = s._snapshot()["inference"]
    assert set(inf["by_device"]) == {"fg-left", "fg-right"}
    assert len(inf["by_device"]["fg-left"]) == 3      # model has 3 outputs


def test_per_role_engines_route_each_band_to_its_own_model(tmp_path):
    # Separate-model-per-hand: --model-left and --model-right load two distinct
    # models; a band tagged 'left' runs the left model, 'right' the right model.
    # The two models have DIFFERENT output counts (3 vs 2) so routing is provable
    # by the length of each band's prediction.
    import time as _t
    from openmuscle.web.state import AppState
    from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION

    left_path, _ = _make_model(tmp_path, n_labels=3, subdir="left")
    right_path, _ = _make_model(tmp_path, n_labels=2, subdir="right")
    s = AppState(udp_port=53889, captures_dir=str(tmp_path),
                 model_left=left_path, model_right=right_path,
                 enable_discovery=False)
    assert s.engine is None                       # no shared model, only per-role
    assert s._has_any_engine() and s.inference_enabled
    # The Sources-panel role tags the router reads (discovery is off in the test).
    s._role_by_device = {"fg-left": "left", "fg-right": "right"}

    def fg(did):
        return OpenMusclePacket(
            version=CURRENT_VERSION, device_type="flexgrid", device_id=did,
            timestamp_ms=0, data={"matrix": [[0.1, 0.3], [0.2, 0.4]]},
            receive_time=_t.time())

    s._handle_packet(fg("fg-left"))
    s._handle_packet(fg("fg-right"))
    inf = s._snapshot()["inference"]
    assert len(inf["by_device"]["fg-left"]) == 3      # left model -> 3 outputs
    assert len(inf["by_device"]["fg-right"]) == 2     # right model -> 2 outputs
    assert "left=" in inf["model"] and "right=" in inf["model"]


def test_untagged_band_falls_back_to_shared_model(tmp_path):
    # A band with no left/right tag uses --model (the shared engine) so single-
    # model mode and mixed setups still predict.
    import time as _t
    from openmuscle.web.state import AppState
    from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION

    shared_path, _ = _make_model(tmp_path, n_labels=3, subdir="shared")
    s = AppState(udp_port=53890, captures_dir=str(tmp_path),
                 model_path=shared_path, enable_discovery=False)
    # No role map entry -> _engine_for_role("") returns the shared engine.
    s._handle_packet(OpenMusclePacket(
        version=CURRENT_VERSION, device_type="flexgrid", device_id="fg-x",
        timestamp_ms=0, data={"matrix": [[0.1, 0.3], [0.2, 0.4]]},
        receive_time=_t.time()))
    inf = s._snapshot()["inference"]
    assert len(inf["by_device"]["fg-x"]) == 3
