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

import math

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split

from openmuscle.data.dataset import load_training_frame, CAPTURE_COLUMN
from openmuscle.ml import evaluate as ev
from openmuscle.ml.registry import ModelRegistry

RANDOM_SPLIT_NOTE = ("optimistic: a shuffled row split, kept only to compare "
                     "with older models; do not use it to judge a model")


# ---------------------------------------------------------------- model size
#
# A random forest keeps one value per label column in EVERY node of EVERY tree.
# A camera hand has 175 label columns, so a fully grown 100-tree forest came to
# about 500 MB for a few minutes of recording (and 900 MB for ten), per band, and
# the whole forest lives in memory while it predicts. Two measures keep a model
# small without touching what the result card scores:
#   * train only the five finger curls (LABEL_SET "curls", the default); the full
#     175 columns stay available on request ("full", e.g. for the 3D ghost hand);
#   * cap the forest: at most MAX_TREES trees and a minimum leaf size that grows
#     with the number of rows so the estimated size stays under a budget.
LABEL_SETS = ("curls", "full")
DEFAULT_LABEL_SET = "curls"
DEFAULT_TREES = 100
MAX_TREES = 100
MIN_SAMPLES_LEAF = 3
# Estimated pickle size we plan for, in MB, per model. 30 MB keeps a two-band
# training near 60 MB (the target is well under 100 MB); the full hand is a
# deliberate choice and gets a larger allowance.
SIZE_BUDGET_MB = {"curls": 30.0, "full": 150.0}
# Size model: a tree has about _NODES_PER_ROW * rows / min_samples_leaf nodes,
# each costing _NODE_BYTES plus 8 bytes per label column. Fitted to real runs
# (7500 rows, 5 labels, leaf 1..8, pickle overhead included).
_NODE_BYTES = 64
_NODES_PER_ROW = 1.1


def plan_forest(n_rows: int, n_outputs: int, n_estimators: int = None,
                label_set: str = DEFAULT_LABEL_SET) -> dict:
    """Pick the tree count and minimum leaf size for a forest of this size.

    Returns {"n_estimators", "min_samples_leaf", "est_mb", "budget_mb"}. The tree
    count is clamped to 1..MAX_TREES. The leaf size starts at MIN_SAMPLES_LEAF
    and rises only as far as needed to bring the estimated model under the size
    budget, so a short recording keeps a fine-grained forest and a long one cannot
    balloon.
    """
    trees = int(n_estimators or DEFAULT_TREES)
    trees = max(1, min(trees, MAX_TREES))
    budget = SIZE_BUDGET_MB.get(label_set, SIZE_BUDGET_MB[DEFAULT_LABEL_SET])
    node_bytes = _NODE_BYTES + 8 * max(1, int(n_outputs))

    def est_mb(leaf):
        return trees * _NODES_PER_ROW * max(1, n_rows) / leaf * node_bytes / 1e6

    leaf = MIN_SAMPLES_LEAF
    if est_mb(leaf) > budget:
        leaf = int(math.ceil(trees * _NODES_PER_ROW * max(1, n_rows) * node_bytes
                             / (budget * 1e6)))
    return {"n_estimators": trees, "min_samples_leaf": leaf,
            "est_mb": round(est_mb(leaf), 1), "budget_mb": budget}


def _new_model(model_type: str, n_estimators: int, min_samples_leaf: int = 1):
    if model_type == "random_forest":
        # n_jobs=-1: the three fits below (held-out, shuffled, final) are the
        # whole training wait; use every core.
        return RandomForestRegressor(n_estimators=n_estimators,
                                     min_samples_leaf=min_samples_leaf,
                                     random_state=42, n_jobs=-1)
    raise ValueError(f"Unknown model type: {model_type}")


def curl_targets(X, y, extras):
    """Reduce a camera/headset hand's 175 label columns to the five finger curls.

    The curls are what the result card scores and what a person reads off the
    hand. The ratified lbl_flex_* columns recorded next to the labels are used
    where present; any row without them gets its curls worked out from the 175
    joint values (hand_angles). Rows whose curls cannot be worked out are dropped
    from X, y and extras together, so the three stay row-aligned.

    Returns (X, y5, extras) with y5 columns named lbl_flex_<finger>.
    """
    flat = y.to_numpy(dtype=float)
    curls = np.full((len(flat), len(ev._FLEX_KEYS)), np.nan)
    for j, key in enumerate(ev._FLEX_KEYS):
        if key in extras:
            curls[:, j] = extras[key].to_numpy(dtype=float)
    missing = ~np.isfinite(curls).all(axis=1)
    if missing.any():
        derived = ev._pose_curls(flat[missing])
        sub = curls[missing]
        sub[~np.isfinite(sub)] = derived[~np.isfinite(sub)]
        curls[missing] = sub
    keep = np.isfinite(curls).all(axis=1)
    if not keep.any():
        raise ValueError("No finger curls could be worked out from the hand "
                         "recording (the hand joints are missing)")
    y5 = pd.DataFrame(curls, columns=list(ev._FLEX_KEYS))
    if keep.all():
        return X, y5, extras
    return (X[keep].reset_index(drop=True), y5[keep].reset_index(drop=True),
            extras[keep].reset_index(drop=True))


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
                n_estimators: int = DEFAULT_TREES, role: str = None,
                holdout_captures: list = None, purge_s: float = 1.0,
                extra_meta: dict = None, labels: str = DEFAULT_LABEL_SET) -> tuple:
    """Train a model from a CSV capture file.

    Args:
        data_path: path to CSV file with sensor + label columns. A file built by
            combine_csvs(capture_column="_capture") lets whole captures be held
            out; any other file is scored on a chronological tail.
        model_type: model type ("random_forest")
        output: explicit output path (overrides registry)
        test_split: fraction held out for evaluation (last captures, or the
            tail of a single capture)
        n_estimators: number of trees for RandomForest (at most MAX_TREES; the
            minimum leaf size is chosen by plan_forest to keep the model small)
        role: left/right -> SEPARATE-MODEL-PER-HAND. Keep only that role's rows
            and train a single-arm model. Run twice for model_left + model_right.
            None = legacy behavior (bilateral pivot if both roles present).
        holdout_captures: capture tags to hold out instead of the last N
            (multi-capture files only).
        purge_s: seconds of rows dropped between train and tail (single capture).
        extra_meta: provenance merged into the model's metadata.json at the top
            level (participant, band ids, captures...). Written for the registry
            copy and for an explicit `output` alike.
        labels: "curls" (default) trains only the five finger curls when the
            labels are a 175-column hand skeleton, which is what the result card
            scores and keeps the model small; "full" keeps all 175 columns (a
            larger model, needed to draw the predicted 3D hand). Labels that are
            not a hand skeleton (a 4-piston LASK5) are trained as recorded.

    Returns:
        (model, metrics_dict). metrics keep r2/mse (now HELD-OUT) and add
        eval_method, eval_captures, n_eval_rows, curl_r2, curl_mae, per_finger,
        n_constant_labels, r2_random_split, label_set, leaf_min_rows. The
        model itself is fit on all rows.
    """
    if labels not in LABEL_SETS:
        raise ValueError(f"labels must be one of {LABEL_SETS}, got {labels!r}")
    X, y, extras = load_training_frame(data_path, role=role)
    # How many recorded label columns never change (a hand skeleton has ~100),
    # counted on what was recorded, before any reduction to the curls.
    n_constant_recorded = int(ev.constant_label_mask(y.to_numpy(dtype=float)).sum())
    label_set = "recorded"
    if y.shape[1] == ev.POSE_LABEL_WIDTH:
        label_set = "full"
        if labels == "curls":
            try:
                X, y, extras = curl_targets(X, y, extras)
                label_set = "curls"
            except ValueError:
                # No usable hand joints at all (a labeler that tracked nothing or
                # sent placeholder values): train on the columns as recorded, as
                # before. Such labels are constant, so the forest stays tiny, and
                # the held-out score reports that there is nothing to score.
                pass
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
    forest = plan_forest(n_rows, y.shape[1], n_estimators,
                         "full" if label_set == "full" else "curls")
    n_trees, leaf = forest["n_estimators"], forest["min_samples_leaf"]
    scores = {"r2": None, "mse": None, "curl_r2": None, "curl_mae": None,
              "per_finger": [], "score_space": None,
              "n_constant_labels": n_constant_recorded}
    n_eval_train = 0
    if plan["method"] != "none":
        tr, te = plan["train_idx"], plan["eval_idx"]
        n_eval_train = len(tr)
        probe = _new_model(model_type, n_trees, leaf)
        print(f"Scoring {model_type} on {len(te)} held-out rows "
              f"({plan['method']}), fit on {len(tr)}...")
        probe.fit(X.iloc[tr], y.iloc[tr])
        canon = extras.iloc[te] if any(c in extras for c in ev._FLEX_KEYS) else None
        scores = ev.score_heldout(y_all[te], probe.predict(X.iloc[te]),
                                  label_cols, y_all, canon_true=canon)
        scores["n_constant_labels"] = n_constant_recorded

    # The shuffled split, for continuity with old numbers only.
    random_scores = {"r2": None, "mse": None}
    if n_rows >= 10:
        Xa, Xb, ya, yb = train_test_split(X, y, test_size=min(0.5, max(0.05, test_split)),
                                          random_state=42)
        shuffled = _new_model(model_type, n_trees, leaf).fit(Xa, ya)
        random_scores = ev.legacy_scores(
            yb.to_numpy(dtype=float), shuffled.predict(Xb),
            ev.constant_label_mask(y_all))

    model = _new_model(model_type, n_trees, leaf)
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
        "n_estimators": n_trees,
        "leaf_min_rows": leaf,
        "est_model_mb": forest["est_mb"],
        "label_set": label_set,
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
