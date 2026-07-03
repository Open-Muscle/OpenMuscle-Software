"""Experiment suite for the OpenMuscle dataset/baseline paper.

Trains per-hand RandomForest baselines (60 FMG cells -> canonical finger
angles) under four evaluation protocols of increasing honesty:

  E1 within-session, random 80/20 split   (the optimistic number)
  E2 within-session, temporal 80/20 split (kills adjacent-frame leakage)
  E3 cross-session, same wearer           (generalization across re-donning)
  E4 cross-wearer                         (generalization across people)

Targets are derived UNIFORMLY from the raw Quest joint labels via
openmuscle.hand_angles (wrist-relative canonical angles: 5x lbl_flex_* in
[0,1] + 10x lbl_ang_* in degrees), so captures recorded before the canonical
columns shipped are comparable with newer ones, and cross-session tests are
not confounded by world-frame position offsets.

Run from the repo root:
    set PYTHONPATH=pc\\src && python analysis/paper/analyze.py
Outputs: analysis/paper/results.json + printed markdown tables.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pc" / "src"))
from openmuscle import hand_angles  # noqa: E402

DATA = REPO / "pc" / "data" / "raw" / "merged"
OUT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Dataset registry: (wearer, session label) -> capture files.
# Clark J2 take 3 (capture_1783113997) is EXCLUDED: 29.5 s simultaneous label
# dead gap (Quest tracking loss, 23% of the take; see the Jul-3 data audit).
# Clark morning capture_1783111868 is EXCLUDED: left-band-only 9.7 s fragment.
SESSIONS = {
    ("tory", "T-jun28"): [
        "capture_1782662414.csv", "capture_1782662954.csv", "capture_1782663139.csv"],
    ("clark", "C-jul3am"): ["capture_1783111763.csv"],
    ("clark", "C-jul3pm"): ["capture_1783113626.csv", "capture_1783113806.csv"],
    ("tory", "T-jul3"): ["capture_1783114480.csv", "capture_1783114592.csv"],
}

FLEX = ["lbl_flex_thumb", "lbl_flex_index", "lbl_flex_middle",
        "lbl_flex_ring", "lbl_flex_pinky"]
ANG = ["lbl_ang_thumb_mcp", "lbl_ang_thumb_ip",
       "lbl_ang_index_mcp", "lbl_ang_index_pip",
       "lbl_ang_middle_mcp", "lbl_ang_middle_pip",
       "lbl_ang_ring_mcp", "lbl_ang_ring_pip",
       "lbl_ang_pinky_mcp", "lbl_ang_pinky_pip"]
TARGETS = FLEX + ANG


def load_session(files):
    """-> {hand: (X, Y, ts)} with canonical targets derived from raw labels."""
    per_hand = {"left": [], "right": []}
    for f in files:
        df = pd.read_csv(DATA / f)
        sensor_cols = [c for c in df.columns
                       if c.startswith("R") and "C" in c[1:] and c[1].isdigit()]
        label_cols = [f"label_{i}" for i in range(175)]
        if not set(label_cols) <= set(df.columns):
            raise SystemExit(f"{f}: missing quest label columns")
        for hand in ("left", "right"):
            part = df[df["role"] == hand]
            if not len(part):
                continue
            X = part[sensor_cols].to_numpy(float)
            ts = part["ts_hub_ms"].to_numpy(float)
            raw = part[label_cols].to_numpy(float)
            Y = np.full((len(part), len(TARGETS)), np.nan)
            for i in range(len(part)):
                canon = hand_angles.canonical_labels_from_flat(raw[i].tolist())
                if canon:
                    Y[i] = [np.nan if canon.get(t) is None else canon[t]
                            for t in TARGETS]
            ok = ~np.isnan(Y).any(axis=1)
            per_hand[hand].append((X[ok], Y[ok], ts[ok]))
    out = {}
    for hand, parts in per_hand.items():
        if parts:
            out[hand] = (np.vstack([p[0] for p in parts]),
                         np.vstack([p[1] for p in parts]),
                         np.concatenate([p[2] for p in parts]))
    return out


def fit(Xtr, Ytr):
    m = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    m.fit(Xtr, Ytr)
    return m


def evaluate(model, Xte, Yte):
    P = model.predict(Xte)
    nf = len(FLEX)
    res = {
        "n_test": int(len(Xte)),
        "flex_mae": float(np.abs(P[:, :nf] - Yte[:, :nf]).mean()),
        "ang_mae_deg": float(np.abs(P[:, nf:] - Yte[:, nf:]).mean()),
        "flex_r2": float(r2_score(Yte[:, :nf], P[:, :nf])),
        "ang_r2": float(r2_score(Yte[:, nf:], P[:, nf:])),
        "per_finger_flex_mae": {
            FLEX[i].replace("lbl_flex_", ""):
                float(np.abs(P[:, i] - Yte[:, i]).mean())
            for i in range(nf)},
    }
    return res


def main():
    t0 = time.time()
    print("Loading sessions + deriving canonical targets...")
    data = {}   # (wearer, session) -> {hand: (X, Y, ts)}
    stats = []
    for key, files in SESSIONS.items():
        data[key] = load_session(files)
        for hand, (X, Y, ts) in data[key].items():
            dur = (ts.max() - ts.min()) / 1000.0
            stats.append({"wearer": key[0], "session": key[1], "hand": hand,
                          "rows": int(len(X)), "duration_s": round(float(dur), 1),
                          "captures": len(files)})
    results = {"dataset": stats, "E1_within_random": [], "E2_within_temporal": [],
               "E3_cross_session": [], "E4_cross_wearer": []}

    rng = np.random.RandomState(42)
    for key, hands in data.items():
        for hand, (X, Y, ts) in hands.items():
            tag = {"wearer": key[0], "session": key[1], "hand": hand}
            # E1: random 80/20
            idx = rng.permutation(len(X))
            cut = int(0.8 * len(X))
            tr, te = idx[:cut], idx[cut:]
            results["E1_within_random"].append(
                {**tag, **evaluate(fit(X[tr], Y[tr]), X[te], Y[te])})
            # E2: temporal 80/20 (train on the first 80% of the take by time)
            order = np.argsort(ts)
            tr, te = order[:cut], order[cut:]
            results["E2_within_temporal"].append(
                {**tag, **evaluate(fit(X[tr], Y[tr]), X[te], Y[te])})
        print(f"  within-session done: {key}")

    # E3: cross-session, same wearer (train the whole source session)
    pairs = [(("tory", "T-jun28"), ("tory", "T-jul3")),
             (("tory", "T-jul3"), ("tory", "T-jun28")),
             (("clark", "C-jul3am"), ("clark", "C-jul3pm")),
             (("clark", "C-jul3pm"), ("clark", "C-jul3am"))]
    for src, dst in pairs:
        for hand in ("left", "right"):
            if hand not in data[src] or hand not in data[dst]:
                continue
            Xtr, Ytr, _ = data[src][hand]
            Xte, Yte, _ = data[dst][hand]
            results["E3_cross_session"].append(
                {"wearer": src[0], "train": src[1], "test": dst[1], "hand": hand,
                 **evaluate(fit(Xtr, Ytr), Xte, Yte)})
        print(f"  cross-session done: {src[1]} -> {dst[1]}")

    # E4: cross-wearer (all sessions of one wearer -> all of the other)
    def wearer_all(w):
        per = {}
        for key, hands in data.items():
            if key[0] != w:
                continue
            for hand, (X, Y, ts) in hands.items():
                if hand in per:
                    per[hand] = (np.vstack([per[hand][0], X]),
                                 np.vstack([per[hand][1], Y]))
                else:
                    per[hand] = (X, Y)
        return per

    tory, clark = wearer_all("tory"), wearer_all("clark")
    for (src_name, src), (dst_name, dst) in [(("tory", tory), ("clark", clark)),
                                             (("clark", clark), ("tory", tory))]:
        for hand in ("left", "right"):
            if hand not in src or hand not in dst:
                continue
            results["E4_cross_wearer"].append(
                {"train": src_name, "test": dst_name, "hand": hand,
                 **evaluate(fit(*src[hand]), *dst[hand])})
        print(f"  cross-wearer done: {src_name} -> {dst_name}")

    OUT.joinpath("results.json").write_text(json.dumps(results, indent=2))
    print(f"\nWrote {OUT / 'results.json'} in {time.time() - t0:.0f}s")

    # ---- printed summary tables (markdown) ----
    def block(name, rows, cols):
        print(f"\n### {name}")
        print("| " + " | ".join(cols) + " |")
        print("|" + "---|" * len(cols))
        for r in rows:
            print("| " + " | ".join(
                (f"{r[c]:.3f}" if isinstance(r.get(c), float) else str(r.get(c, "")))
                for c in cols) + " |")

    block("Dataset", results["dataset"],
          ["wearer", "session", "hand", "rows", "duration_s"])
    for e in ("E1_within_random", "E2_within_temporal"):
        block(e, results[e],
              ["session", "hand", "flex_mae", "ang_mae_deg", "flex_r2", "ang_r2", "n_test"])
    block("E3_cross_session", results["E3_cross_session"],
          ["train", "test", "hand", "flex_mae", "ang_mae_deg", "flex_r2", "n_test"])
    block("E4_cross_wearer", results["E4_cross_wearer"],
          ["train", "test", "hand", "flex_mae", "ang_mae_deg", "flex_r2", "n_test"])


if __name__ == "__main__":
    main()
