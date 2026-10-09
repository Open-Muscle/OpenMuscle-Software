"""Checks run on the selected recordings BEFORE a model is fitted.

Everything a person can get wrong when choosing recordings (too little data,
a labeler that never moved, a band that was not on the arm, left and right
recorded at different times...) is caught here and said in plain words, instead
of surfacing later as a scikit-learn message such as "n_samples=0".

check_training_data() returns a list of dicts:

    {"level": "block" | "warn", "code": str, "message": str, "fix": str}

A "block" means training would fail or produce nothing worth keeping, so the
caller must stop and show the message. A "warn" lets training go ahead and is
shown next to the result. friendly_error() turns any leftover exception from the
data loader or from scikit-learn into the same kind of plain sentence.
"""

import numpy as np
import pandas as pd

from openmuscle.data.dataset import (
    CAPTURE_COLUMN, _pivot_bilateral_frame, detect_columns, is_bilateral_v2,
)
from openmuscle.quality import FLAT_FRACTION_BAD, FLAT_RANGE_COUNTS

# Fewer usable rows than this, or less recorded time than this, cannot hold out
# a fair test and gives a model that has only seen a moment of movement.
MIN_ROWS = 150
MIN_SECONDS = 10.0

# A label column whose spread is below this never moved.
_CONST_EPS = 1e-9

BLOCK = "block"
WARN = "warn"


def _item(level, code, message, fix=""):
    return {"level": level, "code": code, "message": message, "fix": fix}


def blocks(checks):
    return [c for c in (checks or []) if c.get("level") == BLOCK]


def warnings(checks):
    return [c for c in (checks or []) if c.get("level") == WARN]


def join_messages(items, limit=2):
    """One plain paragraph from the first few block messages."""
    return " ".join(c["message"] for c in items[:limit])


# ----------------------------------------------------------------- sides

def plan_sides(roles_by_capture):
    """Decide whether the selection should be trained one hand at a time.

    `roles_by_capture` maps a capture tag to the set of band roles in it. When
    every capture holds exactly one role and both left and right appear, the two
    sides were recorded at different times (there is no moment with both bands
    on), so the only sensible model is one per hand. Returns
    {"left": [tags], "right": [tags]} or None when the selection is not like that.
    """
    left, right = [], []
    for tag, roles in roles_by_capture.items():
        roles = {r for r in roles if r in ("left", "right")}
        if roles == {"left"}:
            left.append(tag)
        elif roles == {"right"}:
            right.append(tag)
        else:
            return None
    if left and right:
        return {"left": left, "right": right}
    return None


def each_side_note():
    return _item(
        WARN, "trained_each_side",
        "The left and right recordings were made at different times, so each "
        "hand is trained on its own recording (two models, one per hand).",
        "To get a test on a separate recording, record each hand at least twice.")


def _roles_by_capture(df):
    if "role" not in df.columns:
        return {}
    col = CAPTURE_COLUMN if CAPTURE_COLUMN in df.columns else None
    if col is None:
        return {"_one": {str(r) for r in df["role"].dropna().unique()}}
    return {str(tag): {str(r) for r in part["role"].dropna().unique()}
            for tag, part in df.groupby(col, sort=False)}


# --------------------------------------------------------------- helpers

def _numeric(df, cols):
    return df[cols].apply(pd.to_numeric, errors="coerce")


def _time_column(df):
    """(values in ms, or None) from the best clock the file carries."""
    if "ts_hub_ms" in df.columns:
        return pd.to_numeric(df["ts_hub_ms"], errors="coerce")
    if "timestamp" in df.columns:
        t = pd.to_numeric(df["timestamp"], errors="coerce")
        if t.notna().any():
            return t * 1000.0 if t.abs().median() < 1e11 else t
    return None


def _duration_s(df):
    """Recorded seconds, summed over captures (a clock can restart between them).
    None when the file has no usable clock."""
    t = _time_column(df)
    if t is None or not t.notna().any():
        return None
    if CAPTURE_COLUMN in df.columns:
        spans = [(t[idx].max() - t[idx].min())
                 for idx in df.groupby(CAPTURE_COLUMN, sort=False).groups.values()
                 if t[idx].notna().any()]
    else:
        spans = [t.max() - t.min()]
    total = float(np.nansum(spans)) / 1000.0
    return max(0.0, total)


def _capture_count(df, meta):
    if CAPTURE_COLUMN in df.columns:
        return int(df[CAPTURE_COLUMN].nunique())
    names = (meta or {}).get("captures")
    return len(names) if names else 1


def _flat_fraction(frame, cols):
    """Share of sensor cells whose range stayed under the flat threshold."""
    if not cols or frame.empty:
        return 0.0
    vals = _numeric(frame, cols).to_numpy(dtype=float)
    rng = np.nanmax(vals, axis=0) - np.nanmin(vals, axis=0)
    rng = np.where(np.isfinite(rng), rng, 0.0)
    return float(np.mean(rng < FLAT_RANGE_COUNTS))


def _fmt_secs(s):
    if s is None:
        return ""
    return "{:.0f} seconds".format(s) if s < 90 else "{:.1f} minutes".format(s / 60.0)


def _list(values):
    values = sorted(str(v) for v in values)
    if len(values) <= 1:
        return "".join(values)
    return ", ".join(values[:-1]) + " and " + values[-1]


# ---------------------------------------------------------- the main check

def check_training_data(df, role=None, meta=None, min_rows=None, min_seconds=None):
    """Look at the combined recordings and say what is wrong with them.

    Args:
        df: the selected captures as one DataFrame (combine_csvs output, with the
            `_capture` column when more than one recording is selected).
        role: 'left' | 'right' for a one-hand model, None for the pooled path.
        meta: optional {"captures": [names], "capture_meta": {name: meta dict}}
            for the checks that compare recordings (sessions, participants).
        min_rows, min_seconds: override MIN_ROWS / MIN_SECONDS (tests).

    Returns the list of check dicts described in the module docstring.
    """
    min_rows = MIN_ROWS if min_rows is None else min_rows
    min_seconds = MIN_SECONDS if min_seconds is None else min_seconds
    out = []

    if df is None or len(df) == 0:
        return [_item(BLOCK, "no_rows",
                      "The selected recordings are empty, so there is nothing to train on.",
                      "Pick a recording that has data in it, or record a new one.")]

    sensor_cols, label_cols, problem = _columns(df)
    if problem:
        return [problem]

    # ---- one hand, or both -------------------------------------------------
    plan = None
    if role is None and "role" in df.columns:
        plan = plan_sides(_roles_by_capture(df))
    if plan:
        # Left and right were recorded at different times: each hand trains on its
        # own recordings. Check every side on its own and say that is what happens.
        out.append(each_side_note())
        for side, tags in plan.items():
            part = df[df[CAPTURE_COLUMN].astype(str).isin(tags)] \
                if CAPTURE_COLUMN in df.columns else df
            sub_meta = _sub_meta(meta, tags)
            for c in check_training_data(part, role=side, meta=sub_meta,
                                         min_rows=min_rows, min_seconds=min_seconds):
                c = dict(c)
                if c["level"] == BLOCK:
                    c["message"] = "{} hand: {}".format(side.capitalize(), c["message"])
                out.append(c)
        return out

    side_name = None
    rows = df
    if role:
        side_name = role
        if "role" not in df.columns:
            return [_item(BLOCK, "no_band_roles",
                          "These recordings do not say which arm each band was on, so a "
                          "{} hand model cannot be picked out of them.".format(role),
                          "Record again with the band set to left or right.")]
        rows = df[df["role"].astype(str) == role]
        if rows.empty:
            have = sorted({str(r) for r in df["role"].dropna().unique()})
            return [_item(BLOCK, "no_rows_for_side",
                          "None of the selected recordings has a {} band in it{}.".format(
                              role, " (they have: {})".format(_list(have)) if have else ""),
                          "Pick a recording made with the {} band, or train the other hand.".format(role))]

    bilateral = role is None and is_bilateral_v2(df)
    sides = [(role, sensor_cols)]
    if bilateral:
        try:
            rows = _pair_rows(df)
        except ValueError:
            rows = df.iloc[0:0]
        if rows.empty:
            return [_item(BLOCK, "no_paired_rows",
                          "The left and right bands were never recorded at the same moment, "
                          "so there are no paired samples to train a two-band model on.",
                          "Pick one hand's recording and train that hand on its own.")]
        sides = [("left", [c + "_L" for c in sensor_cols]),
                 ("right", [c + "_R" for c in sensor_cols])]

    # ---- labels ------------------------------------------------------------
    y = _numeric(rows, label_cols)
    if y.isna().all().all():
        return [_item(BLOCK, "labels_all_nan",
                      "The finger labeler gave no readings in these recordings (every label "
                      "is empty), so there is nothing to learn from.",
                      "Check the webcam can see your hand (or the finger labeler is "
                      "connected) and record again.")]
    sensors = _numeric(rows, [c for _s, cols in sides for c in cols])
    complete = y.notna().all(axis=1) & sensors.notna().all(axis=1)
    n_dropped = int((~complete).sum())
    rows = rows[complete.to_numpy()]
    y = y[complete.to_numpy()]
    if rows.empty:
        return [_item(BLOCK, "labels_missing",
                      "Every recorded row has a gap in the finger labels (the labeler "
                      "kept losing your hand), so none of it can be used.",
                      "Keep your hand fully in view of the labeler and record again.")]
    if n_dropped:
        out.append(_item(
            WARN, "nan_rows_dropped",
            "{} row{} with a missing reading {} left out.".format(
                n_dropped, "" if n_dropped == 1 else "s",
                "was" if n_dropped == 1 else "were"),
            "Occasional gaps are normal; many of them mean the labeler lost your hand."))

    # ---- enough data -------------------------------------------------------
    n = len(rows)
    dur = _duration_s(rows)
    too_short = dur is not None and dur < min_seconds
    if n < min_rows or too_short:
        have = "{} usable sample{}".format(n, "" if n == 1 else "s")
        if dur is not None:
            have += " (about {})".format(_fmt_secs(dur))
        out.append(_item(
            BLOCK, "too_few_rows",
            "There are only {} to train on{}. A model needs at least {} samples "
            "(roughly {:.0f} seconds) of movement.".format(
                have, " for the {} hand".format(side_name) if side_name else "",
                min_rows, min_seconds),
            "Record a longer take, about a minute of slowly opening and closing "
            "each finger, then try again."))

    # ---- labeler moved -----------------------------------------------------
    spread = np.ptp(y.to_numpy(dtype=float), axis=0)
    if bool((spread < _CONST_EPS).all()):
        out.append(_item(
            BLOCK, "constant_labels",
            "The finger labeler never moved in these recordings, so there is nothing "
            "for the model to learn.",
            "Check the labeler can see your hand (or is connected), move your fingers "
            "while recording, and try again."))

    # ---- band on the arm ---------------------------------------------------
    for side, cols in sides:
        if _flat_fraction(rows, cols) >= FLAT_FRACTION_BAD:
            who = "The {} band".format(side) if (bilateral and side) else \
                  ("The {} band".format(side_name) if side_name else "The band")
            out.append(_item(
                BLOCK, "flat_band",
                "{} was not touching the arm: nearly every sensor stayed flat the whole "
                "time.".format(who),
                "Check the band sits snug on the forearm with the sensor side against "
                "the skin, then record again."))

    # ---- warnings that do not stop training ---------------------------------
    n_caps = _capture_count(df, meta)
    if n_caps == 1:
        out.append(_item(
            WARN, "single_capture",
            "Only one recording was used, so the score is measured on the end of that "
            "same take.",
            "Record two or more separate takes for a fairer test."))

    if "device_id" in df.columns:
        for r, part in ([(role, rows)] if role else
                        [(str(k), g) for k, g in df.groupby("role")]
                        if "role" in df.columns else [(None, df)]):
            ids = {str(d) for d in part["device_id"].dropna().unique()}
            if len(ids) > 1:
                out.append(_item(
                    WARN, "mixed_bands",
                    "The {}band data comes from {} different band units ({}). "
                    "Mixing units usually lowers accuracy.".format(
                        "{} ".format(r) if r else "", len(ids), _list(ids)),
                    "Train on one band unit's recordings at a time."))

    out.extend(_mixed_origin_warnings(meta))
    # Blocks first so callers can show the stopper first.
    out.sort(key=lambda c: 0 if c["level"] == BLOCK else 1)
    return out


def _columns(df):
    """(sensor_cols, label_cols, None) or (None, None, a block item)."""
    try:
        sensor_cols, label_cols = detect_columns(df)
        return sensor_cols, label_cols, None
    except ValueError as e:
        if "label" in str(e):
            return None, None, _item(
                BLOCK, "no_labels",
                "These recordings have no finger labels (they were saved without a "
                "labeler), so a model cannot learn from them.",
                "Record again with the webcam or finger labeler turned on.")
        return None, None, _item(
            BLOCK, "no_sensors",
            "These recordings have no band sensor readings in them.",
            "Record again with a band connected.")


def _pair_rows(df):
    """The left rows with a right row at the same moment (the training pivot)."""
    if CAPTURE_COLUMN in df.columns:
        parts = [_pivot_bilateral_frame(p, extra_cols=(CAPTURE_COLUMN,))
                 for _n, p in df.groupby(CAPTURE_COLUMN, sort=False)
                 if is_bilateral_v2(p)]
        if not parts:
            raise ValueError("no paired rows")
        merged = pd.concat(parts, ignore_index=True)
    else:
        merged = _pivot_bilateral_frame(df)
    return merged


def _sub_meta(meta, tags):
    """The meta restricted to the captures in `tags` (a capture's tag is its name)."""
    if not meta:
        return meta
    keep = set(tags)
    cm = meta.get("capture_meta") or {}
    names = [n for n in (meta.get("captures") or []) if n in keep] or list(tags)
    return {"captures": names,
            "capture_meta": {n: cm[n] for n in names if n in cm}}


def _mixed_origin_warnings(meta):
    """Compare recordings by session, participant and day (from their meta)."""
    out = []
    cm = (meta or {}).get("capture_meta") or {}
    if len(cm) < 2:
        return out
    sessions, people, days = set(), set(), set()
    for m in cm.values():
        m = m or {}
        auto = m.get("auto") or {}
        if auto.get("session_id"):
            sessions.add(str(auto["session_id"]))
        who = auto.get("session_wearer") or m.get("subject")
        if who:
            people.add(str(who).strip().lower())
        ms = m.get("created_ms")
        if ms:
            try:
                days.add(pd.to_datetime(int(ms), unit="ms").strftime("%Y-%m-%d"))
            except (TypeError, ValueError, OverflowError):
                pass
    if len(people) > 1:
        out.append(_item(
            WARN, "mixed_participants",
            "These recordings are from different participants ({}). A model "
            "usually works best for the person it was trained on.".format(_list(people)),
            "Train one participant at a time."))
    if len(sessions) > 1 or len(days) > 1:
        what = "different sessions" if len(sessions) > 1 else "different days"
        if len(sessions) > 1 and len(days) > 1:
            what = "different sessions and days"
        out.append(_item(
            WARN, "mixed_sessions",
            "These recordings come from {}. If the band was moved between them the "
            "model may be less accurate.".format(what),
            "Put the band back in the same spot each time, or train one session at a time."))
    return out


# ------------------------------------------------------------ error mapping

def friendly_error(exc):
    """A plain sentence for an exception raised while loading or fitting.

    Never returns scikit-learn or pandas wording ("n_samples=0", "Input y
    contains NaN"); anything unrecognised gets a generic message and the caller
    logs the raw text.
    """
    text = str(exc)
    low = text.lower()
    if "n_samples" in low or "0 sample" in low or "minimum of 1 is required" in low \
            or "empty" in low and "frame" in low:
        return ("There were no usable samples to train on after lining the recordings up. "
                "Check the selected recordings overlap in time and have data in them.")
    if "nan" in low or "infinity" in low or "contains nan" in low:
        return ("Some readings in these recordings are missing, so the model could not be "
                "fitted. Record again and keep your hand in view of the labeler.")
    if "no sensor columns" in low or "no label columns" in low:
        return ("These recordings are missing the band sensors or the finger labels, so a "
                "model cannot learn from them.")
    if "refusing to merge" in low or "different schemas" in low:
        return ("These recordings were made with different setups (for example a different "
                "labeler, or bands with a different number of sensors), so they cannot be "
                "trained together. Pick recordings that were made the same way.")
    if "no rows with role" in low or "requested but the csv has no" in low:
        return ("The selected recordings do not contain the band for that hand. Pick a "
                "recording made with that band, or train the other hand.")
    if "pivot_bilateral" in low or "paired" in low:
        return ("The left and right bands were never recorded at the same moment, so a "
                "two-band model cannot be built. Train each hand on its own.")
    return ("The recordings could not be used for training. Try a different selection, or "
            "record a new take.")


def csv_roles(path):
    """The band roles present in one capture CSV (empty set when it has no role
    column, e.g. a single-band file from an older version)."""
    try:
        col = pd.read_csv(path, usecols=["role"])["role"]
    except (ValueError, KeyError, OSError):
        return set()
    return {str(r) for r in col.dropna().unique()}
