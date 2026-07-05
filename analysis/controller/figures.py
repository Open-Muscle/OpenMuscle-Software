"""Figure for the controller-decode findings: random vs temporal analog R2 per
hand, next to how little the strong-signal controls were used."""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent
R = json.loads((OUT / "results.json").read_text())

plt.rcParams.update({"figure.dpi": 150, "font.size": 10})
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.6))

# Left: analog R2 random vs temporal, per hand.
hands = [h["hand"] for h in R["hands"]]
rand = [h["per_split"]["random"]["analog_r2"] for h in R["hands"]]
temp = [h["per_split"]["temporal"]["analog_r2"] for h in R["hands"]]
x = np.arange(len(hands)); w = 0.36
ax1.bar(x - w / 2, rand, w, label="random split (leaky)", color="#8bc34a", edgecolor="black", linewidth=0.6)
ax1.bar(x + w / 2, temp, w, label="temporal split (honest)", color="#ff5a6b", edgecolor="black", linewidth=0.6)
ax1.axhline(0, color="black", lw=0.8)
ax1.set_xticks(x); ax1.set_xticklabels([h + " band" for h in hands])
ax1.set_ylabel("analog decode R2 (sticks + triggers)")
ax1.set_title("Decode does not generalize across time")
ax1.legend(fontsize=8)

# Right: control usage (why). Percent of frames each analog control was active.
labels = ["LStick", "RStick", "LT", "RT"]
usage = [21.2, 12.0, 0.1, 0.2]   # from the usage scan (max stick axis per hand)
colors = ["#6cf", "#6cf", "#ffa42c", "#ffa42c"]
ax2.bar(labels, usage, color=colors, edgecolor="black", linewidth=0.6)
ax2.set_ylabel("% of frames the control was active")
ax2.set_title("The strong-signal controls (triggers) were unused")
for i, v in enumerate(usage):
    ax2.text(i, v + 0.4, f"{v:.1f}%", ha="center", fontsize=8)

fig.tight_layout()
fig.savefig(OUT / "controller_decode.png")
print("wrote", OUT / "controller_decode.png")
