"""Honest held-out evaluation for the trainer.

Two pieces, both pure (arrays in, plain dicts out) so they are easy to test:

  plan_holdout()    decides WHICH rows are held out. Never a shuffled row split:
                    a shuffled split puts near-identical neighbouring frames on
                    both sides and reports a flattering score for a model that
                    has only memorised the recording. Instead we hold out whole
                    captures (the last ones recorded), or, with a single capture,
                    a chronological tail separated from the training rows by a
                    short purge gap.

  score_heldout()   scores the held-out predictions. The legacy r2/mse are kept
                    (constant label columns no longer count, they used to be
                    "predicted perfectly" and inflate the average), and the
                    headline number is curl_r2: per-finger R2 in finger-curl
                    space, the thing a person actually cares about.
"""

import math

import numpy as np

from openmuscle.hand_angles import per_finger_flexion, joints_from_flat

# Smallest split that still means something. Below this the trainer reports
# eval_method "none" instead of a number it cannot stand behind.
MIN_TRAIN_ROWS = 8
MIN_EVAL_ROWS = 3

# Pose-label captures (Quest / webcam) store 25 joints x 7 channels.
POSE_LABEL_WIDTH = 175
POSE_FINGERS = ("thumb", "index", "middle", "ring", "pinky")
LASK5_FINGERS = ("index", "middle", "ring", "pinky")      # pistons 0..3
_FLEX_KEYS = tuple("lbl_flex_" + f for f in POSE_FINGERS)

# A column whose spread is below this carries no signal to score.
_CONST_EPS = 1e-9


def _num(v):
    """JSON-safe float (None for NaN / inf / None)."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ---------------------------------------------------------------- the split

def _median_dt_ms(times):
    if times is None or len(times) < 3:
        return None
    d = np.diff(np.asarray(times, dtype=float))
    d = d[np.isfinite(d) & (d > 0)]
    return float(np.median(d)) if len(d) else None


def plan_holdout(n_rows, captures=None, times_ms=None, test_split=0.2,
                 purge_s=1.0, holdout_captures=None):
    """Choose the held-out rows.

    Args:
        n_rows: number of training rows.
        captures: per-row capture tag (sequence of str) or None for one capture.
        times_ms: per-row time in ms (any monotone clock) for sizing the purge.
        test_split: fraction held out (whole captures: round(k * fraction),
            at least one; tail: that fraction of the rows).
        purge_s: seconds of rows dropped between the end of the training rows
            and the start of the tail, so the tail is not a continuation of the
            last training frames. Capped at 10% of the rows.
        holdout_captures: explicit capture tags to hold out (multi-capture).

    Returns a dict: method ('held_out_capture' | 'held_out_tail' | 'none'),
    train_idx, eval_idx (int arrays), eval_captures (list), purge_rows (int).
    """
    n = int(n_rows)
    frac = min(0.5, max(0.05, float(test_split or 0.2)))
    idx = np.arange(n)
    caps = None if captures is None else np.asarray([str(c) for c in captures])
    order = list(dict.fromkeys(caps.tolist())) if caps is not None else []

    if len(order) >= 2:
        chosen = [c for c in order if holdout_captures and c in set(holdout_captures)]
        if not chosen or len(chosen) >= len(order):
            k = max(1, min(len(order) - 1, int(round(len(order) * frac))))
            chosen = order[-k:]
        mask = np.isin(caps, chosen)
        if (n - int(mask.sum()) >= MIN_TRAIN_ROWS
                and int(mask.sum()) >= MIN_EVAL_ROWS):
            return {"method": "held_out_capture", "train_idx": idx[~mask],
                    "eval_idx": idx[mask], "eval_captures": chosen,
                    "purge_rows": 0}

    n_eval = max(MIN_EVAL_ROWS, int(math.ceil(n * frac)))
    dt = _median_dt_ms(times_ms)
    purge = int(math.ceil(purge_s * 1000.0 / dt)) if dt else 0
    purge = max(0, min(purge, int(n * 0.10)))
    n_train = n - n_eval - purge
    if n_train < MIN_TRAIN_ROWS:
        return {"method": "none", "train_idx": idx, "eval_idx": idx[:0],
                "eval_captures": [], "purge_rows": 0}
    eval_idx = idx[n - n_eval:]
    tail_caps = (list(dict.fromkeys(caps[eval_idx].tolist()))
                 if caps is not None else [])
    return {"method": "held_out_tail", "train_idx": idx[:n_train],
            "eval_idx": eval_idx, "eval_captures": tail_caps,
            "purge_rows": purge}


# ---------------------------------------------------------------- the scores

def _r2(t, p):
    """R2 of one column, None when the truth does not vary (undefined)."""
    if len(t) < 2 or np.ptp(t) < _CONST_EPS:
        return None
    ss_tot = float(((t - t.mean()) ** 2).sum())
    if ss_tot <= 0:
        return None
    return 1.0 - float(((t - p) ** 2).sum()) / ss_tot


def constant_label_mask(y_all):
    """True for label columns that never change across the whole selection."""
    y_all = np.asarray(y_all, dtype=float)
    return np.ptp(y_all, axis=0) < _CONST_EPS


def legacy_scores(y_true, y_pred, const_mask):
    """Plain r2 / mse averaged over the label columns that actually vary.

    Constant columns used to be scored too and counted as perfect (a hand
    skeleton has ~100 of them), which pulled the average up by about half."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    keep = ~np.asarray(const_mask, dtype=bool)
    if y_true.ndim != 2 or not keep.any() or len(y_true) == 0:
        return {"r2": None, "mse": None}
    t, p = y_true[:, keep], y_pred[:, keep]
    r2s = [_r2(t[:, j], p[:, j]) for j in range(t.shape[1])]
    r2s = [r for r in r2s if r is not None]
    return {"r2": _num(np.mean(r2s)) if r2s else None,
            "mse": _num(np.mean((t - p) ** 2))}


def _pose_curls(flat):
    """(n, 5) finger curls [0,1] (thumb..pinky) from flat 175-wide pose labels;
    NaN where a row has too few joints."""
    out = np.full((len(flat), 5), np.nan)
    for i, row in enumerate(np.asarray(flat, dtype=float)):
        flex = per_finger_flexion(joints_from_flat(row.tolist()))
        if flex:
            for j, key in enumerate(_FLEX_KEYS):
                v = flex.get(key)
                if v is not None:
                    out[i, j] = v
    return out


def _unit_range(y_all):
    """(lo, hi) per column for normalising to ~0..1; already-0..1 data is left
    alone so a piston that only used part of its travel is not stretched."""
    y_all = np.asarray(y_all, dtype=float)
    lo, hi = y_all.min(axis=0), y_all.max(axis=0)
    inside = (lo >= -1e-9) & (hi <= 1.0 + 1e-9)
    lo = np.where(inside, 0.0, lo)
    hi = np.where(inside, 1.0, hi)
    span = np.where(hi - lo < _CONST_EPS, 1.0, hi - lo)
    return lo, span


def _finger_rows(names, t, p):
    """Per-finger {finger, r2, mae, constant} rows plus the two averages."""
    rows = []
    for j, name in enumerate(names):
        ok = np.isfinite(t[:, j]) & np.isfinite(p[:, j])
        tj, pj = t[ok, j], p[ok, j]
        r2 = _r2(tj, pj) if len(tj) else None
        mae = float(np.mean(np.abs(tj - pj))) if len(tj) else None
        rows.append({"finger": name, "r2": _num(r2), "mae": _num(mae),
                     "constant": bool(len(tj) and r2 is None)})
    r2s = [r["r2"] for r in rows if r["r2"] is not None]
    maes = [r["mae"] for r in rows if r["mae"] is not None]
    return (rows, _num(np.mean(r2s)) if r2s else None,
            _num(np.mean(maes)) if maes else None)


def score_heldout(y_true, y_pred, label_cols, y_all, canon_true=None):
    """Score held-out predictions.

    Args:
        y_true, y_pred: (n_eval, n_labels) arrays, in the model's raw label space.
        label_cols: label column names.
        y_all: (n_rows, n_labels) all labels in the selection (constant-column
            detection and piston normalisation).
        canon_true: optional DataFrame of the held-out rows' lbl_flex_* columns
            (used directly instead of re-deriving curls when present).

    Returns dict: r2, mse (legacy, constant columns ignored), n_constant_labels,
    curl_r2, curl_mae, per_finger (list of {finger, r2, mae, constant}) and
    score_space ('curl' for pose labels or trained curls, 'piston' for 4-piston LASK5 labels,
    'label' for anything else).
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    const = constant_label_mask(y_all)
    out = dict(legacy_scores(y_true, y_pred, const))
    out["n_constant_labels"] = int(const.sum())
    n_labels = y_true.shape[1] if y_true.ndim == 2 else 0

    if tuple(str(c) for c in label_cols) == _FLEX_KEYS:
        # A model trained on the five finger curls directly (the default for a
        # hand skeleton): the labels already are the curls, nothing to derive.
        space, names = "curl", POSE_FINGERS
        t, p = y_true, y_pred
    elif n_labels == POSE_LABEL_WIDTH:
        space, names = "curl", POSE_FINGERS
        truth = None
        if canon_true is not None and all(k in canon_true.columns for k in _FLEX_KEYS):
            cand = np.column_stack(
                [np.asarray(canon_true[k], dtype=float) for k in _FLEX_KEYS])
            if np.isfinite(cand).any():
                truth = cand
        if truth is None:
            truth = _pose_curls(y_true)
        t, p = truth, _pose_curls(y_pred)
    elif n_labels == len(LASK5_FINGERS):
        space, names = "piston", LASK5_FINGERS
        lo, span = _unit_range(y_all)
        t, p = (y_true - lo) / span, (y_pred - lo) / span
    else:
        # Not a hand: score each varying label in 0..1 units, no finger rows.
        space = "label"
        keep = ~const
        if not keep.any() or len(y_true) < 2:
            out.update(curl_r2=None, curl_mae=None, per_finger=[], score_space=space)
            return out
        lo, span = _unit_range(np.asarray(y_all, dtype=float)[:, keep])
        t = (y_true[:, keep] - lo) / span
        p = (y_pred[:, keep] - lo) / span
        _rows, r2, mae = _finger_rows([str(c) for c in np.asarray(label_cols)[keep]], t, p)
        out.update(curl_r2=r2, curl_mae=mae, per_finger=[], score_space=space)
        return out

    rows, r2, mae = _finger_rows(names, t, p)
    out.update(curl_r2=r2, curl_mae=mae, per_finger=rows, score_space=space)
    return out
