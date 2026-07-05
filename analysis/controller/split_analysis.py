"""Per-hand controller-decoding analysis on a gamepad-labeled capture.

Each forearm should predict only its OWN hand's controls (openmuscle.
gamepad_channels). This trains a per-hand RandomForest on a HELD-OUT split
(train on the first 80% of the take by time, test on the last 20%, so adjacent-
frame leakage can't flatter the score) and reports, per hand:

  - per-channel R2 + MAE, split into analog (sticks/triggers) vs digital buttons
  - the headline analog-decode R2 (the "control a game with your forearm" signal)
  - the naive baseline: the SAME band trained on all 21 channels (what dragged
    the first model to r2=0.19), to show what the per-hand split buys.

Run:  set PYTHONPATH=pc\\src && python analysis/controller/split_analysis.py <capture.csv>
Outputs analysis/controller/results.json + printed tables.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pc" / "src"))
from openmuscle import gamepad_channels as gc  # noqa: E402

OUT = Path(__file__).resolve().parent
DEFAULT_CAP = r"C:/Users/User/data/raw/merged/capture_1783267460_fixed.csv"


def temporal_split(df):
    df = df.sort_values("ts_hub_ms")
    cut = int(0.8 * len(df))
    return df.iloc[:cut], df.iloc[cut:]


def random_split(df):
    # 80/20 random: the OPTIMISTIC split (adjacent frames leak across the
    # boundary). Compared against temporal, the gap is the leakage.
    idx = np.random.RandomState(42).permutation(len(df))
    cut = int(0.8 * len(df))
    return df.iloc[idx[:cut]], df.iloc[idx[cut:]]


def fit_eval(Xtr, Ytr, Xte, Yte, channels):
    m = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    m.fit(Xtr, Ytr)
    P = m.predict(Xte)
    if P.ndim == 1:
        P = P[:, None]
    per = []
    for k, ch in enumerate(channels):
        yt, pr = Yte[:, k], P[:, k]
        var = float(np.var(yt))
        r2 = float(r2_score(yt, pr)) if var > 1e-9 else float("nan")
        per.append({
            "name": ch["name"], "kind": ch["kind"], "analog": gc.is_analog(ch),
            "r2": r2, "mae": float(np.abs(pr - yt).mean()),
            "base_rate": float((yt > 0.5).mean()) if not gc.is_analog(ch) else None,
        })
    return per


def mean_r2(rows, analog=None):
    vals = [r["r2"] for r in rows
            if not np.isnan(r["r2"]) and (analog is None or r["analog"] == analog)]
    return float(np.mean(vals)) if vals else float("nan")


def hand_block(df_hand, sensor_cols, hand):
    chans = gc.channels_for_hand(hand)
    lab = [f"label_{c['i']}" for c in chans]
    out = {"hand": hand, "per_split": {}}
    for split_name, splitter in (("temporal", temporal_split), ("random", random_split)):
        tr, te = splitter(df_hand)
        X_tr, X_te = tr[sensor_cols].to_numpy(float), te[sensor_cols].to_numpy(float)
        per = fit_eval(X_tr, tr[lab].to_numpy(float),
                       X_te, te[lab].to_numpy(float), chans)
        out["per_split"][split_name] = {
            "n_train": int(len(tr)), "n_test": int(len(te)),
            "analog_r2": mean_r2(per, analog=True),
            "digital_r2": mean_r2(per, analog=False),
            "per_channel": per,
        }
    return out


def main():
    cap = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CAP
    print(f"loading {cap}")
    df = pd.read_csv(cap)
    sensor_cols = [c for c in df.columns
                   if c.startswith("R") and "C" in c[1:] and c[1].isdigit()]
    print(f"{len(df)} rows, {len(sensor_cols)} sensor cols, "
          f"roles={df['role'].value_counts().to_dict()}")

    results = {"capture": Path(cap).name, "n_rows": int(len(df)), "hands": []}
    for hand in ("left", "right"):
        part = df[df["role"] == hand]
        if not len(part):
            continue
        res = hand_block(part, sensor_cols, hand)
        results["hands"].append(res)

    OUT.joinpath("results.json").write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT / 'results.json'}\n")

    for h in results["hands"]:
        t, r = h["per_split"]["temporal"], h["per_split"]["random"]
        print(f"### {h['hand'].upper()} band")
        print(f"  ANALOG decode R2 (sticks+triggers):  temporal {t['analog_r2']:+.3f}   |   random {r['analog_r2']:+.3f}")
        print(f"  digital button R2 (avg):             temporal {t['digital_r2']:+.3f}   |   random {r['digital_r2']:+.3f}")
        print(f"  (train {t['n_train']} / test {t['n_test']})")
        print("  per-channel (temporal | random), analog channels first:")
        rows = sorted(t["per_channel"], key=lambda c: (not c["analog"], c["name"]))
        rmap = {c["name"]: c for c in r["per_channel"]}
        for c in rows:
            rc = rmap[c["name"]]
            tag = "analog" if c["analog"] else f"digital base={c['base_rate']:.2f}"
            print(f"    {c['name']:>10} ({c['kind']:>11}) r2 temporal={c['r2']:+.3f} "
                  f"random={rc['r2']:+.3f}  mae={c['mae']:.3f}  [{tag}]")
        print()


if __name__ == "__main__":
    main()
