"""Capture-quality primitives: cell-engagement tracking and the take verdict.

The dud-take trap: capture_1779731309 looks like a normal file
(plausible per-cell baselines, paired labels) but 50/60 cells have a value RANGE
under 5 counts across the whole take -- the band was effectively not being worn /
not engaged, so the file trains nothing. Match-rate can't catch this (labels
matched fine); only per-cell activity can.

CellActivityTracker keeps per-cell min/max over a recording (cheap: one compare
pair per cell per frame) so the live verdict can flag a flat take IN PROGRESS,
and the same logic doubles as the offline regression check.

The bottom half of this module turns those stats into the good / check / bad
verdict (with plain-English reasons) that is persisted per take and read by the
dashboard, so the thresholds live in exactly one place.
"""

from openmuscle.take_limits import MIN_TAKE_ROWS, MIN_TAKE_SECONDS, advice

# A cell whose (max - min) stays under this many ADC counts across the take is
# "flat" (baseline noise only; the dud's 50 flat cells all sat under 5).
FLAT_RANGE_COUNTS = 5

# Verdict rule: after enough frames to be meaningful, a take with at least this
# fraction of its cells flat means the band is not engaged.
FLAT_FRACTION_BAD = 0.8
MIN_FRAMES_FOR_VERDICT = 20


class CellActivityTracker:
    """Per-cell min/max over the flattened sensor rows of one recording."""

    def __init__(self):
        self._min = None
        self._max = None
        self.frames = 0

    def update(self, flat_row) -> None:
        """Feed one flattened sensor row (row-major R{r}C{c} values)."""
        if not flat_row:
            return
        if self._min is None:
            self._min = list(flat_row)
            self._max = list(flat_row)
        else:
            n = min(len(flat_row), len(self._min))
            for i in range(n):
                v = flat_row[i]
                if v < self._min[i]:
                    self._min[i] = v
                elif v > self._max[i]:
                    self._max[i] = v
        self.frames += 1

    @property
    def n_cells(self) -> int:
        return len(self._min) if self._min else 0

    def flat_cells(self, threshold: float = FLAT_RANGE_COUNTS) -> int:
        """How many cells never moved more than `threshold` counts."""
        if not self._min:
            return 0
        return sum(1 for lo, hi in zip(self._min, self._max)
                   if (hi - lo) < threshold)

    def is_flat_take(self) -> bool:
        """True when this take is a capture_1779731309-style dud: enough frames
        seen and >= FLAT_FRACTION_BAD of the cells never engaged."""
        n = self.n_cells
        if self.frames < MIN_FRAMES_FOR_VERDICT or n == 0:
            return False
        return self.flat_cells() >= FLAT_FRACTION_BAD * n


# ---------------------------------------------------------------------------
# Label activity (is the labeler producing any motion at all?)
# ---------------------------------------------------------------------------

# A label column "moved" when its value range over the take reaches at least
# this much (label units: meters for a headset, normalized for a camera, counts
# for a finger labeler) AND at least LABEL_MOVE_REL of its own magnitude, so
# integer ADC jitter on a big raw value does not count as motion.
LABEL_MOVE_MIN = 0.02
LABEL_MOVE_REL = 0.01

# A take whose labeler moved fewer than this fraction of its columns is "idle":
# the file pairs fine but the labels are a constant, so there is nothing to
# learn (labeler asleep, not worn, hand out of view).
LABEL_IDLE_FRACTION = 0.1


class LabelActivityTracker:
    """Per-label-column min/max over the label vectors of one recording."""

    def __init__(self):
        self._min = None
        self._max = None
        self.frames = 0

    def update(self, label_values) -> None:
        if not label_values:
            return
        if self._min is None:
            self._min = list(label_values)
            self._max = list(label_values)
        else:
            n = min(len(label_values), len(self._min))
            for i in range(n):
                v = label_values[i]
                if v < self._min[i]:
                    self._min[i] = v
                elif v > self._max[i]:
                    self._max[i] = v
        self.frames += 1

    @property
    def n_cols(self) -> int:
        return len(self._min) if self._min else 0

    def moved_cols(self) -> int:
        """How many label columns moved by more than the noise floor."""
        if not self._min:
            return 0
        moved = 0
        for lo, hi in zip(self._min, self._max):
            need = max(LABEL_MOVE_MIN, LABEL_MOVE_REL * max(abs(lo), abs(hi)))
            if (hi - lo) >= need:
                moved += 1
        return moved


def worst_band(trackers) -> dict:
    """Engagement of the least-engaged band. `trackers` maps a band key (device
    id) to its CellActivityTracker. A bilateral take is judged on its WORST band
    (a flat left band must not hide behind an engaged right one). Returns
    {flat_cells, cells_total, band_flat}."""
    best = None
    for t in trackers.values():
        n = t.n_cells
        if n == 0:
            continue
        frac = t.flat_cells() / n
        if best is None or frac > best[0]:
            best = (frac, t)
    if best is None:
        return {"flat_cells": 0, "cells_total": 0, "band_flat": False}
    t = best[1]
    return {"flat_cells": t.flat_cells(), "cells_total": t.n_cells,
            "band_flat": t.is_flat_take()}


# ---------------------------------------------------------------------------
# Verdict: good | check | bad, with plain-English reasons
# ---------------------------------------------------------------------------
# Single home of the thresholds. The dashboard, the post-stop card and the
# captures table all read the dict this returns; no verdict logic lives in JS.

MATCH_RATE_BAD = 0.5          # labels paired with fewer than half the frames
MATCH_RATE_CHECK = 0.9
MIN_SECONDS = MIN_TAKE_SECONDS    # shorter takes are probably an accidental click
MIN_ROWS = MIN_TAKE_ROWS
HAND_LOST_CHECK = 0.05        # any padded row also trips "check"
HAND_LOST_BAD = 0.5
NO_LABEL_FRAMES = 20          # frames seen with zero matches = labels never came

# Short words for the table badges (code -> word).
FLAG_WORDS = {
    "empty": "empty",
    "short": "too short",
    "weak_signal": "weak signal",
    "no_labels": "no labels",
    "low_match": "labels missing",
    "idle_labeler": "idle labeler",
    "hand_lost": "hand lost",
}


def _flag(code, severity, reason):
    return {"code": code, "word": FLAG_WORDS[code], "severity": severity,
            "reason": reason}


def _pct(x):
    return "{}%".format(int(round(x * 100)))


def evaluate_take(stats: dict) -> dict:
    """Turn a take's raw stats into {verdict, reasons, flags}.

    Recognised keys (all optional; a missing key skips its rule):
      rows, duration_s            size of the take
      has_labeler                 False for a sensor-only take
      match_rate, matched, sensor_frames_seen   live pairing counters
      flat_cells, cells_total, band_flat        worst band engagement
      label_cols, label_cols_moved              label activity
      label_width_mismatch, hand_lost_rows      hand-tracking dropouts
      live                        True while recording (skips size / idle rules)
    """
    s = stats or {}
    flags = []
    rows = s.get("rows")
    dur = s.get("duration_s")
    seen = s.get("sensor_frames_seen") or 0
    has_labeler = s.get("has_labeler")
    # "live" = a take still in progress: do not nag about size or label motion
    # before there is anything to judge.
    live = bool(s.get("live"))
    empty = rows is not None and rows <= 0 and not live

    if empty:
        flags.append(_flag("empty", "bad",
                           "Nothing was recorded. Were the band and labeler sending?"))
    elif not live:
        short_s = dur is not None and dur < MIN_SECONDS
        short_r = rows is not None and rows < MIN_ROWS
        if short_s:
            flags.append(_flag("short", "check",
                               "Only {} seconds recorded. Too short to train on. "
                               "{}".format(int(round(dur)), advice())))
        elif short_r:
            flags.append(_flag("short", "check",
                               "Only {} rows recorded. Too short to train on. "
                               "{}".format(rows, advice())))

    if s.get("band_flat"):
        flags.append(_flag("weak_signal", "bad",
                           "Band did not move: {} of {} cells flat. Was it on the "
                           "arm?".format(s.get("flat_cells", 0), s.get("cells_total", 0))))

    if has_labeler is False:
        flags.append(_flag("no_labels", "check",
                           "No labeler was used, so this take cannot be used to "
                           "train a model."))
    elif not empty:
        rate = s.get("match_rate")
        if rate is not None and s.get("matched") == 0 and seen > NO_LABEL_FRAMES:
            flags.append(_flag("no_labels", "bad",
                               "No labels arrived during the take. Is the labeler "
                               "on and sending?"))
        elif rate is not None and rate < MATCH_RATE_BAD:
            flags.append(_flag("low_match", "bad",
                               "Labels matched only {} of frames.".format(_pct(rate))))
        elif rate is not None and rate < MATCH_RATE_CHECK:
            flags.append(_flag("low_match", "check",
                               "Labels matched only {} of frames.".format(_pct(rate))))

        n_cols = s.get("label_cols") or 0
        too_early = live and (dur is None or dur < MIN_SECONDS)
        if (n_cols and not too_early
                and (s.get("label_cols_moved") or 0) / n_cols < LABEL_IDLE_FRACTION):
            flags.append(_flag("idle_labeler", "check",
                               "The labeler barely moved during the take, so there is "
                               "no motion to learn from. Was the hand moving?"))

        mism = s.get("label_width_mismatch") or 0
        lost = max(s.get("hand_lost_rows") or 0, mism)
        denom = rows or s.get("matched") or 0
        if lost > 0 and denom:
            frac = min(1.0, lost / float(denom))
            if frac >= HAND_LOST_BAD:
                flags.append(_flag("hand_lost", "bad",
                                   "Hand tracking was lost on {} of {} frames ({}).".format(
                                       lost, denom, _pct(frac))))
            elif mism > 0 or frac >= HAND_LOST_CHECK:
                flags.append(_flag("hand_lost", "check",
                                   "Hand tracking dropped on {} of {} frames ({}); those "
                                   "rows have filled-in label values.".format(
                                       lost, denom, _pct(frac))))

    flags.sort(key=lambda f: f["severity"] != "bad")    # worst problems first
    if any(f["severity"] == "bad" for f in flags):
        verdict = "bad"
    elif flags:
        verdict = "check"
    else:
        verdict = "good"
    return {"verdict": verdict,
            "reasons": [f["reason"] for f in flags],
            "flags": flags}


# Stat keys kept next to the verdict when a take is persisted.
_PERSISTED_STATS = (
    "flat_cells", "cells_total", "band_flat", "label_cols", "label_cols_moved",
    "label_idle", "hand_lost_rows", "hand_lost_frac", "label_width_mismatch",
    "has_labeler")


def summarize_take(stats: dict) -> dict:
    """evaluate_take() plus the raw stats it was computed from, in the shape
    stored under meta auto.final (the UI never recomputes any of it)."""
    q = evaluate_take(stats)
    out = {k: stats[k] for k in _PERSISTED_STATS if k in stats}
    out["verdict"] = q["verdict"]
    out["reasons"] = q["reasons"]
    out["flags"] = q["flags"]
    return out


def quality_of(final):
    """Compact {verdict, reasons, flags} out of a persisted auto.final dict;
    None when the take predates persisted quality."""
    if not isinstance(final, dict) or final.get("verdict") not in ("good", "check", "bad"):
        return None
    return {"verdict": final["verdict"],
            "reasons": list(final.get("reasons") or []),
            "flags": list(final.get("flags") or [])}


# ---------------------------------------------------------------------------
# Offline scan (legacy captures with no stored stats)
# ---------------------------------------------------------------------------

_HAND_POSE_SOURCES = ("quest", "quest_hand", "webcam", "webcam_hand")


def _is_sensor_col(name: str) -> bool:
    """True for an R{r}C{c} sensor column header."""
    if len(name) < 4 or name[0] != "R":
        return False
    r, sep, c = name[1:].partition("C")
    return bool(sep) and r.isdigit() and c.isdigit()


def scan_capture_csv(path, label_source=None) -> dict:
    """Stream a capture CSV once and return the stats dict the live recorder
    builds (minus pairing counters, which a finished file cannot tell us).
    Handles schema v1 (`timestamp`, seconds) and v2 (`ts_hub_ms`)."""
    import csv

    trackers = {}
    labels = LabelActivityTracker()
    hand_pose = label_source in _HAND_POSE_SOURCES
    rows = 0
    lost = 0
    t_lo = t_hi = None
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        s_idx = [i for i, c in enumerate(header) if _is_sensor_col(c)]
        l_idx = [i for i, c in enumerate(header) if c.startswith("label_")]
        dev_i = header.index("device_id") if "device_id" in header else None
        if "ts_hub_ms" in header:
            ts_i, ts_scale = header.index("ts_hub_ms"), 1000.0
        elif "timestamp" in header:
            ts_i, ts_scale = header.index("timestamp"), 1.0
        else:
            ts_i, ts_scale = None, 1.0
        for row in reader:
            if not row:
                continue
            rows += 1
            try:
                sensor = [float(row[i]) for i in s_idx]
            except (ValueError, IndexError):
                sensor = None
            if sensor:
                key = row[dev_i] if dev_i is not None and dev_i < len(row) else ""
                tr = trackers.get(key)
                if tr is None:
                    tr = trackers[key] = CellActivityTracker()
                tr.update(sensor)
            if l_idx:
                try:
                    lab = [float(row[i]) for i in l_idx]
                except (ValueError, IndexError):
                    lab = None
                if lab:
                    labels.update(lab)
                    if hand_pose and not any(lab):
                        lost += 1
            if ts_i is not None and ts_i < len(row):
                try:
                    t = float(row[ts_i]) / ts_scale
                except ValueError:
                    continue
                if t_lo is None or t < t_lo:
                    t_lo = t
                if t_hi is None or t > t_hi:
                    t_hi = t

    band = worst_band(trackers)
    n_cols = labels.n_cols
    moved = labels.moved_cols()
    return {
        "rows": rows,
        "duration_s": round(t_hi - t_lo, 1) if t_lo is not None else None,
        "has_labeler": bool(l_idx),
        "flat_cells": band["flat_cells"],
        "cells_total": band["cells_total"],
        "band_flat": band["band_flat"],
        "label_cols": n_cols,
        "label_cols_moved": moved,
        "label_idle": bool(n_cols and moved / n_cols < LABEL_IDLE_FRACTION),
        "hand_lost_rows": lost,
        "hand_lost_frac": round(lost / rows, 3) if rows else 0.0,
    }
