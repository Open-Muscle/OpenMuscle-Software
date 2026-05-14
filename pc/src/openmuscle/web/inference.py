"""Inference engine wrapper for the web UI's ML prediction panel.

Loads a trained sklearn model (typically `data/models/random_forest_*/model.pkl`)
plus its metadata sidecar, and exposes a `predict()` method that takes a
FlexGrid matrix (as a [cols][rows] list-of-lists, the same shape the device
sends) and returns a list of predicted piston values.

Wiring is in `web/state.py`:
 - the engine is constructed when `openmuscle web --model PATH` is given,
 - `_handle_packet` calls `engine.predict(matrix)` on each flexgrid packet,
 - the result is stored on AppState and surfaced through `_inference_snapshot()`,
 - if `--hand HOST[:PORT]` is also set, the result is forwarded over UDP to
   the robot hand in its `PC,a1,a2,a3,a4,a5` CSV format.
"""

import json
import pickle
from pathlib import Path
from typing import Optional


class InferenceEngine:
    """Wraps a trained model with shape validation and graceful failure.

    The model is expected to be an sklearn regressor producing a row of
    floats per input row -- typically 4 piston values per FlexGrid frame.
    n_features is read from the sibling `metadata.json` when present; if
    not, it's pulled from the model's own `n_features_in_` attribute.
    """

    def __init__(self, model_path: str):
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        with open(self.model_path, "rb") as f:
            self.model = pickle.load(f)

        # Sidecar metadata.json next to model.pkl (the registry layout)
        self.metadata: dict = {}
        meta_path = self.model_path.parent / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    self.metadata = json.load(f)
            except Exception:
                pass

        # Expected input feature count. Prefer the model's own attribute
        # (most accurate), fall back to metadata.
        self.n_features: Optional[int] = getattr(self.model, "n_features_in_", None)
        if self.n_features is None:
            self.n_features = self.metadata.get("metrics", {}).get("n_features")

        # Best-effort label count
        self.n_labels: int = self.metadata.get("metrics", {}).get("n_labels", 4)

        # Counters for status reporting
        self.predict_count = 0
        self.last_error: Optional[str] = None
        self.last_prediction: Optional[list] = None

    @property
    def name(self) -> str:
        """Human-readable identifier for the UI snapshot."""
        # Prefer the parent directory name (e.g. "random_forest_20260321_110750")
        return self.model_path.parent.name or self.model_path.name

    def predict(self, matrix) -> Optional[list]:
        """Run a single prediction on a [cols][rows] FlexGrid matrix.

        Returns a list of predicted values (typically length 4) or None if
        shape mismatched or the underlying model raised. None means "no
        prediction this frame" -- the caller should NOT replace its cached
        last_prediction so the UI keeps showing the most recent good one.
        """
        if not matrix:
            return None

        # Flatten row-major (matches the same convention as the CSV writer:
        # R0C0, R0C1, ..., R0Cn, R1C0, ...). The device sends matrix[cols][rows],
        # so iterate rows then cols.
        cols = len(matrix)
        rows = len(matrix[0]) if cols else 0
        flat = [matrix[c][r] for r in range(rows) for c in range(cols)]

        if self.n_features is not None and len(flat) != self.n_features:
            self.last_error = (
                "frame has {} sensors but model expects {}"
                .format(len(flat), self.n_features)
            )
            return None

        try:
            # sklearn models want shape (1, n_features). Avoid importing
            # numpy here -- a list-of-lists works for predict() and we
            # don't want to pay numpy import cost on workers that don't
            # need it.
            raw_pred = self.model.predict([flat])
        except Exception as e:
            self.last_error = "predict() raised: {}".format(e)
            return None

        # Unwrap to a flat list of floats
        try:
            row = list(raw_pred[0])
        except Exception:
            row = list(raw_pred)
        result = [float(v) for v in row]

        self.predict_count += 1
        self.last_prediction = result
        self.last_error = None
        return result
