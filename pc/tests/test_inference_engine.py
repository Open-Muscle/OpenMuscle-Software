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


def _make_model(tmp_path, named=True):
    rng = np.random.RandomState(0)
    X = pd.DataFrame(rng.rand(20, 4), columns=_COLS)
    y = pd.DataFrame(rng.rand(20, 3), columns=["label_0", "label_1", "label_2"])
    model = RandomForestRegressor(n_estimators=5, random_state=0)
    model.fit(X if named else X.values, y)     # named -> feature_names_in_ set
    d = tmp_path / "m"
    d.mkdir()
    with open(d / "model.pkl", "wb") as f:
        pickle.dump(model, f)
    (d / "metadata.json").write_text(
        json.dumps({"metrics": {"n_features": 4, "n_labels": 3}}))
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
