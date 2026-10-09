"""ML model training pipeline.

Refactored from TrainModel/Train_Model_From_Data.py.

The score this trainer reports is HELD-OUT in time, not a shuffled row split:
neighbouring frames of a recording are nearly identical, so a random split lets
the forest memorise the take and reports a number that does not survive the next
recording. We hold out whole captures (or a chronological tail with a purge gap
for a single capture), score in finger-curl space, then fit the shipped model on
ALL rows. The old shuffled number is still reported as r2_random_split, clearly
labelled optimistic.
"""

from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split

from openmuscle.data.dataset import load_training_frame, CAPTURE_COLUMN
from openmuscle.ml import evaluate as ev
from openmuscle.ml.registry import ModelRegistry

RANDOM_SPLIT_NOTE = ("optimistic: a shuffled row split, kept only to compare "
                     "with older models; do not use it to judge a model")


def _new_model(model_type: str, n_estimators: int):
    if model_type == "random_forest":
        # n_jobs=-1: the three fits below (held-out, shuffled, final) are the
        # whole training wait; use every core.
        return RandomForestRegressor(n_estimators=n_estimators,
                                     random_state=42, n_jobs=-1)
    raise ValueError(f"Unknown model type: {model_type}")


def _write_output_metadata(output, model, model_type, metrics, extra_meta):
    """Put metadata.json next to an explicit `-o model.pkl`, so `openmuscle web
    --model-left model_left.pkl` finds the feature count, hand and plain verdict
    the same way it does for a registry model.

    Two files are written: <stem>.metadata.json (always this model's, so
    model_left.pkl and model_right.pkl can share a folder) and metadata.json (the
    name the registry layout uses; the newest write wins when several models
    share the folder). The inference engine prefers the per-stem file.
    """
    import json
    from datetime import datetime
    from openmuscle.ml.verdict import describe_result

    clean = {k: v for k, v in metrics.items() if k != "saved_path"}
    meta = {
        "name": model_type,
        "created": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "dir": output.parent.name,
        "model_type": type(model).__name__,
        "metrics": clean,
        "verdict": describe_result(clean, hand_side=clean.get("role")),
    }
    if extra_meta:
        meta.update(extra_meta)
    for fname in (output.stem + ".metadata.json", "metadata.json"):
        with open(output.parent / fname, "w") as f:
            json.dump(meta, f, indent=2)


def train_model(data_path: str, model_type: str = "random_forest",
                output: str = None, test_split: float = 0.2,
                n_estimators: int = 100, role: str = None,
                holdout_captures: list = None, purge_s: float = 1.0,
                extra_meta: dict = None) -> tuple:
    """Train a model from a CSV capture file.

    Args:
        data_path: path to CSV file with sensor + label columns. A file built by
            combine_csvs(capture_column="_capture") lets whole captures be held
            out; any other file is scored on a chronological tail.
        model_type: model type ("random_forest")
        output: explicit output path (overrides registry)
        test_split: fraction held out for evaluation (last captures, or the
            tail of a single capture)
        n_estimators: number of trees for RandomForest
        role: left/right -> SEPARATE-MODEL-PER-HAND. Keep only that role's rows
            and train a single-arm model. Run twice for model_left + model_right.
            None = legacy behavior (bilateral pivot if both roles present).
        holdout_captures: capture tags to hold out instead of the last N
            (multi-capture files only).
        purge_s: seconds of rows dropped between train and tail (single capture).
        extra_meta: provenance merged into the model's metadata.json at the top
            level (participant, band ids, captures...). Written for the registry
            copy and for an explicit `output` alike.

    Returns:
        (model, metrics_dict). metrics keep r2/mse (now HELD-OUT) and add
        eval_method, eval_captures, n_eval_rows, curl_r2, curl_mae, per_finger,
        n_constant_labels, r2_random_split. The model itself is fit on all rows.
    """
    X, y, extras = load_training_frame(data_path, role=role)
    sensor_cols = list(X.columns)
    label_cols = list(y.columns)
    if role:
        print(f"Role filter: {role} -> {len(X)} rows, single-arm model")

    n_rows = len(X)
    caps = extras[CAPTURE_COLUMN].tolist() if CAPTURE_COLUMN in extras else None
    times = extras["_time_ms"].to_numpy() if "_time_ms" in extras else None
    plan = ev.plan_holdout(n_rows, captures=caps, times_ms=times,
                           test_split=test_split, purge_s=purge_s,
                           holdout_captures=holdout_captures)

    y_all = y.to_numpy(dtype=float)
    scores = {"r2": None, "mse": None, "curl_r2": None, "curl_mae": None,
              "per_finger": [], "score_space": None,
              "n_constant_labels": int(ev.constant_label_mask(y_all).sum())}
    n_eval_train = 0
    if plan["method"] != "none":
        tr, te = plan["train_idx"], plan["eval_idx"]
        n_eval_train = len(tr)
        probe = _new_model(model_type, n_estimators)
        print(f"Scoring {model_type} on {len(te)} held-out rows "
              f"({plan['method']}), fit on {len(tr)}...")
        probe.fit(X.iloc[tr], y.iloc[tr])
        canon = extras.iloc[te] if any(c in extras for c in ev._FLEX_KEYS) else None
        scores = ev.score_heldout(y_all[te], probe.predict(X.iloc[te]),
                                  label_cols, y_all, canon_true=canon)

    # The shuffled split, for continuity with old numbers only.
    random_scores = {"r2": None, "mse": None}
    if n_rows >= 10:
        Xa, Xb, ya, yb = train_test_split(X, y, test_size=min(0.5, max(0.05, test_split)),
                                          random_state=42)
        shuffled = _new_model(model_type, n_estimators).fit(Xa, ya)
        random_scores = ev.legacy_scores(
            yb.to_numpy(dtype=float), shuffled.predict(Xb),
            ev.constant_label_mask(y_all))

    model = _new_model(model_type, n_estimators)
    print(f"Training {model_type} on {n_rows} samples (all rows)...")
    model.fit(X, y)

    metrics = {
        "mse": scores["mse"],
        "r2": scores["r2"],
        "eval_method": plan["method"],
        "eval_captures": plan["eval_captures"],
        "n_eval_rows": len(plan["eval_idx"]),
        "n_eval_train": n_eval_train,
        "purge_rows": plan["purge_rows"],
        "curl_r2": scores["curl_r2"],
        "curl_mae": scores["curl_mae"],
        "per_finger": scores["per_finger"],
        "score_space": scores["score_space"],
        "n_constant_labels": scores["n_constant_labels"],
        "r2_random_split": random_scores["r2"],
        "mse_random_split": random_scores["mse"],
        "random_split_note": RANDOM_SPLIT_NOTE,
        "n_rows": n_rows,
        "n_train": n_rows,                       # the shipped model sees every row
        "n_test": len(plan["eval_idx"]),         # rows the score was measured on
        "n_features": len(sensor_cols),
        "n_labels": len(label_cols),
        "sensor_columns": sensor_cols,
        "label_columns": label_cols,
        "model_type": model_type,
        "n_estimators": n_estimators,
        "role": role,
    }

    registry = ModelRegistry()
    if output:
        import pickle
        from pathlib import Path
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        with open(output, "wb") as f:
            pickle.dump(model, f)
        _write_output_metadata(Path(output), model, model_type, metrics, extra_meta)
        metrics["saved_path"] = str(output)
        print(f"Model saved to {output}")
    else:
        saved_path = registry.save(model, model_type, metrics, role=role,
                                   extra=extra_meta)
        metrics["saved_path"] = str(saved_path)
        print(f"Model saved to {saved_path}")

    if metrics["eval_method"] == "none":
        print("Held-out score: not enough rows to hold any out "
              f"({n_rows} rows); record more and retrain.")
    else:
        cr2 = metrics["curl_r2"]
        print("Held-out ({}, {} rows)  curl R2: {}  MAE: {}  (legacy R2 {}, MSE {})".format(
            metrics["eval_method"], metrics["n_eval_rows"],
            "n/a" if cr2 is None else f"{cr2:.3f}",
            "n/a" if metrics["curl_mae"] is None else f"{metrics['curl_mae']:.3f}",
            "n/a" if metrics["r2"] is None else f"{metrics['r2']:.3f}",
            "n/a" if metrics["mse"] is None else f"{metrics['mse']:.4f}"))
    return model, metrics
