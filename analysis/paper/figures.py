"""Figures for the OpenMuscle dataset/baseline paper, from results.json.

Fig 1: the generalization ladder (flex MAE across the four protocols).
Fig 2: per-finger flexion MAE, within-session temporal vs cross-wearer.
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent
R = json.loads((OUT / "results.json").read_text())

plt.rcParams.update({"figure.dpi": 150, "font.size": 10})


def mean_of(rows, key):
    vals = [r[key] for r in rows]
    return float(np.mean(vals)), float(np.std(vals))


# ---- Fig 1: generalization ladder --------------------------------------
conds = [
    ("Within-session\nrandom split", R["E1_within_random"]),
    ("Within-session\ntemporal split", R["E2_within_temporal"]),
    ("Cross-session\nsame wearer", R["E3_cross_session"]),
    ("Cross-wearer", R["E4_cross_wearer"]),
]
means, stds = zip(*[mean_of(rows, "flex_mae") for _, rows in conds])
fig, ax = plt.subplots(figsize=(6.4, 3.6))
colors = ["#2cd16a", "#8bc34a", "#ffa42c", "#ff5a6b"]
bars = ax.bar(range(len(conds)), means, yerr=stds, capsize=4,
              color=colors, edgecolor="black", linewidth=0.6)
ax.set_xticks(range(len(conds)))
ax.set_xticklabels([c for c, _ in conds])
ax.set_ylabel("Finger-flexion MAE (fraction of full curl)")
ax.set_title("Generalization ladder: evaluation protocol vs error")
for b, m in zip(bars, means):
    ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.012,
            f"{m:.3f}", ha="center", fontsize=9)
ax.axhline(0.25, color="gray", ls="--", lw=0.8)
ax.text(0.02, 0.255, "chance-like (~range/4)", fontsize=8, color="gray")
ax.set_ylim(0, 0.36)
fig.tight_layout()
fig.savefig(OUT / "fig1_generalization_ladder.png")
print("wrote fig1")

# ---- Fig 2: per-finger flexion MAE --------------------------------------
fingers = ["thumb", "index", "middle", "ring", "pinky"]


def per_finger(rows):
    return [float(np.mean([r["per_finger_flex_mae"][f] for r in rows]))
            for f in fingers]


within = per_finger(R["E2_within_temporal"])
crossw = per_finger(R["E4_cross_wearer"])
x = np.arange(len(fingers))
w = 0.38
fig, ax = plt.subplots(figsize=(6.4, 3.4))
ax.bar(x - w / 2, within, w, label="Within-session (temporal split)",
       color="#8bc34a", edgecolor="black", linewidth=0.6)
ax.bar(x + w / 2, crossw, w, label="Cross-wearer",
       color="#ff5a6b", edgecolor="black", linewidth=0.6)
ax.set_xticks(x)
ax.set_xticklabels([f.capitalize() for f in fingers])
ax.set_ylabel("Flexion MAE (fraction of full curl)")
ax.set_title("Per-finger error: within-session vs cross-wearer")
ax.legend(fontsize=9)
fig.tight_layout()
fig.savefig(OUT / "fig2_per_finger.png")
print("wrote fig2")
