"""Website animation: real vs predicted VR hand from forearm pressure.

Uses the best-performing configuration (right hand, wearer T, Jul-3 session)
HONESTLY: the model trains on take 1 (capture_1783114480) and the animation
runs it on a segment of take 2 (capture_1783114592), which it never saw.
Green skeleton = VR-tracked ground truth; amber = predicted from the 60
forearm pressure cells only. Both wrist-localized (same transform as the
live viewers). Output: hand_prediction.gif.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image
from sklearn.ensemble import RandomForestRegressor

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "pc" / "data" / "raw" / "merged"
OUT = Path(__file__).resolve().parent / "hand_prediction.gif"

TRAIN_CSV = "capture_1783114480.csv"
TEST_CSV = "capture_1783114592.csv"
HAND = "right"
FPS = 12
SECONDS = 12

# WebXR 25-joint connectivity (wrist=0; thumb 1-4; then 5 per finger).
BONES = [(0, 1), (1, 2), (2, 3), (3, 4),
         (0, 5), (5, 6), (6, 7), (7, 8), (8, 9),
         (0, 10), (10, 11), (11, 12), (12, 13), (13, 14),
         (0, 15), (15, 16), (16, 17), (17, 18), (18, 19),
         (0, 20), (20, 21), (21, 22), (22, 23), (23, 24)]


def load(csv, hand):
    df = pd.read_csv(DATA / csv)
    part = df[df["role"] == hand]
    sensor_cols = [c for c in df.columns
                   if c.startswith("R") and "C" in c[1:] and c[1].isdigit()]
    label_cols = [f"label_{i}" for i in range(175)]
    X = part[sensor_cols].to_numpy(float)
    Y = part[label_cols].to_numpy(float)
    ts = part["ts_hub_ms"].to_numpy(float)
    ok = np.abs(Y).sum(axis=1) > 1e-6          # drop label-dropout rows
    return X[ok], Y[ok], ts[ok]


def quat_inv_rotate(q, v):
    """Rotate vectors v (N,3) by the INVERSE of unit quaternion q=(x,y,z,w)."""
    x, y, z, w = q
    q = np.array([-x, -y, -z, w])              # inverse of a unit quat
    u, s = q[:3], q[3]
    return (2.0 * np.dot(v, u)[:, None] * u
            + (s * s - np.dot(u, u)) * v
            + 2.0 * s * np.cross(np.broadcast_to(u, v.shape), v) * -1)


def wrist_local(flat):
    """175 floats -> (25,3) wrist-local joint positions."""
    j = flat.reshape(25, 7)
    pos = j[:, :3] - j[0, :3]
    q = j[0, 3:7]
    n = np.linalg.norm(q)
    if n > 1e-6:
        pos = quat_inv_rotate(q / n, pos)
    return pos


def main():
    print("training on take 1...")
    Xtr, Ytr, _ = load(TRAIN_CSV, HAND)
    model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    model.fit(Xtr, Ytr)

    Xte, Yte, ts = load(TEST_CSV, HAND)
    # Pick the liveliest SECONDS-long window (max label motion = best demo).
    n = len(Xte)
    hz = n / ((ts[-1] - ts[0]) / 1000.0)
    win = int(SECONDS * hz)
    best, best_score = 0, -1
    for s in range(0, n - win, max(1, win // 8)):
        score = Yte[s:s + win].std(axis=0).mean()
        if score > best_score:
            best, best_score = s, score
    seg = slice(best, best + win)
    Xs, Ys = Xte[seg], Yte[seg]
    print(f"segment rows {best}..{best + win} of {n} ({hz:.1f} Hz), predicting...")
    Ps = model.predict(Xs)
    mae = float(np.abs(Ps - Ys).mean())
    print(f"segment MAE (raw label units, m/quat): {mae:.4f}")

    # Downsample the segment's frames to FPS.
    step = max(1, int(round(hz / FPS)))
    idxs = list(range(0, len(Xs), step))

    real_pts = np.stack([wrist_local(Ys[i]) for i in idxs])
    pred_pts = np.stack([wrist_local(Ps[i]) for i in idxs])
    # Robust cubic framing: a single bad predicted wrist quaternion can fling
    # points far out and shrink the hand to a speck; frame on the 1st-99th
    # percentile of the REAL hand instead and let outliers clip.
    allr = real_pts.reshape(-1, 3)
    center = np.median(allr, axis=0)
    half = 1.02 * np.percentile(np.abs(allr - center), 99)
    lims = [(center[d] - half, center[d] + half) for d in range(3)]

    frames = []
    fig = plt.figure(figsize=(5.4, 5.4), dpi=90)
    ax = fig.add_subplot(111, projection="3d")
    fig.text(0.5, 0.965, "OpenMuscle: predicting the hand from forearm pressure",
             ha="center", fontsize=10, color="#1a1a1a", weight="bold", zorder=10)
    fig.text(0.5, 0.93, "green = VR-tracked truth   ·   amber = predicted from "
             "60 pressure cells (unseen take)",
             ha="center", fontsize=8, color="#555555", zorder=10)
    for k, (R, P) in enumerate(zip(real_pts, pred_pts)):
        ax.cla()
        # Oversize the 3D pane past the figure edges: the projected cube
        # otherwise sits in generous margins and the hand renders tiny.
        ax.set_position([-0.18, -0.20, 1.36, 1.36])
        ax.set_xlim(*lims[0]); ax.set_ylim(*lims[1]); ax.set_zlim(*lims[2])
        ax.set_axis_off()
        for a, b in BONES:
            ax.plot(*zip(R[a], R[b]), color="#34d399", lw=2.6)
            ax.plot(*zip(P[a], P[b]), color="#fbbf24", lw=2.0, alpha=0.85)
        ax.scatter(*R.T, color="#34d399", s=16)
        ax.scatter(*P.T, color="#fbbf24", s=11, alpha=0.85)
        ax.view_init(elev=18, azim=-55 + 12 * np.sin(2 * np.pi * k / len(idxs)))
        fig.canvas.draw()
        buf = np.asarray(fig.canvas.buffer_rgba())[..., :3]
        frames.append(Image.fromarray(buf))
    plt.close(fig)

    frames[0].save(OUT, save_all=True, append_images=frames[1:],
                   duration=int(1000 / FPS), loop=0, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(frames)} frames)")


if __name__ == "__main__":
    main()
