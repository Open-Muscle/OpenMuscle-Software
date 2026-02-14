import socket
import os
import time
import ast
import threading
import csv
from collections import deque
from queue import Queue
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

"""
FlexGrid_Merged_Receiver.py  (robust matcher + raw logger)
========================================================
1️⃣  One UDP port (default **3141**) shared by
    • FlexGrid bracelet (4×16 matrix)
    • LASK5 labeler   (4-element piston set)

2️⃣  Dumps **every** datagram to *Data-Captures/raw_packets_<ts>.txt* so we
    can audit packet loss independently of pairing logic.

3️⃣  Matching algorithm – finds the *nearest-timestamp* LASK5 sample on
    either side of each FlexGrid packet within ±100 ms.

4️⃣  Live visual: heat-map (FlexGrid) + bar chart (current LASK5 target).

5️⃣  Console every second:  FlexGrid N   LASK5 M   CSV rows R   Unpaired U
"""

###############################################################################
# CONFIGURABLE CONSTANTS                                                     #
###############################################################################
UDP_IP = "0.0.0.0"
PORT = 3141                     # change if needed
PAIR_WINDOW = 0.100             # seconds (±100 ms)
SAVE_DIR = "Data-Captures"

MATRIX_ROWS = 4
MATRIX_COLS = 16
LABEL_COUNT = 4

# Visual scaling for LASK5 bar chart
# If labeler sends calibrated values, set peak (e.g. 1.0 or 400)
LABEL_VMAX = 1

###############################################################################
# FILE HANDLES (CSV + raw log)                                               #
###############################################################################
os.makedirs(SAVE_DIR, exist_ok=True)
STAMP = int(time.time())
CSV_PATH = os.path.join(SAVE_DIR, f"flexgrid_merged_{STAMP}.csv")
RAW_PATH = os.path.join(SAVE_DIR, f"raw_packets_{STAMP}.txt")

csv_fp = open(CSV_PATH, "w", newline="")
csv_writer = csv.writer(csv_fp)
csv_writer.writerow([
    "timestamp",
    *[f"R{r}C{c}" for r in range(MATRIX_ROWS) for c in range(MATRIX_COLS)],
    *[f"label_{i}" for i in range(LABEL_COUNT)],
])

raw_fp = open(RAW_PATH, "w", encoding="utf-8")
raw_lock = threading.Lock()

###############################################################################
# STATE                                                                     #
###############################################################################
packet_q      = Queue()
label_buffer  = deque()   # holds (ts, data) for recent LASK5 packets
latest_label  = None      # last displayed on bar chart

flex_cnt      = 0
label_cnt     = 0
rows_written  = 0
unpaired_cnt  = 0
last_report   = time.time()

pressure_mat = np.zeros((MATRIX_ROWS, MATRIX_COLS))

###############################################################################
# RECEIVER THREAD                                                            #
###############################################################################

def receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((UDP_IP, PORT))
    except PermissionError as e:
        print(f"[ERROR] Cannot bind {UDP_IP}:{PORT} – {e}\nSee docs for fixes.")
        return

    print(f"Listening on {UDP_IP}:{PORT}")
    while True:
        data, _ = sock.recvfrom(8192)
        ts = time.time()
        raw = data.decode(errors="replace")
        with raw_lock:
            raw_fp.write(f"{ts} {raw}\n")
        try:
            pkt = ast.literal_eval(raw)
            packet_q.put((ts, pkt))
        except Exception:
            continue

a = threading.Thread(target=receiver, daemon=True)
a.start()

###############################################################################
# MATPLOTLIB SETUP                                                           #
###############################################################################
fig, (ax_heat, ax_bar) = plt.subplots(1, 2, figsize=(8, 4),
                                     gridspec_kw={"width_ratios": [4, 1]})

im = ax_heat.imshow(pressure_mat, cmap="plasma", vmin=0, vmax=4096)
ax_heat.set_title("FlexGrid Pressures")
plt.colorbar(im, ax=ax_heat, fraction=0.046, pad=0.04)

bars = ax_bar.bar(range(LABEL_COUNT), [0] * LABEL_COUNT)
ax_bar.set_ylim(0, LABEL_VMAX)
ax_bar.set_xticks(range(LABEL_COUNT))
ax_bar.set_title("LASK5 Targets (calibrated)")

###############################################################################
# HELPER – prune old label samples                                           #
###############################################################################

def prune_labels(cutoff_ts: float):
    while label_buffer and label_buffer[0][0] < cutoff_ts - PAIR_WINDOW:
        label_buffer.popleft()

###############################################################################
# ANIMATION DRIVER                                                           #
###############################################################################

def step(_frame):
    global latest_label, flex_cnt, label_cnt, rows_written, unpaired_cnt, last_report
    updated_heat = False
    updated_bar  = False

    while not packet_q.empty():
        ts, pkt = packet_q.get()

        if isinstance(pkt, dict) and pkt.get("id") == "OM-LASK5":
            label_cnt += 1
            label_buffer.append((ts, pkt.get("data", [None]*LABEL_COUNT)))
            latest_label = label_buffer[-1]
            updated_bar = True
            continue

        matrix = None
        if isinstance(pkt, list):
            matrix = pkt
        elif isinstance(pkt, dict) and "matrix" in pkt:
            matrix = pkt["matrix"]
        if matrix is None or not (len(matrix) == MATRIX_COLS and all(len(col) == MATRIX_ROWS for col in matrix)):
            continue

        flex_cnt += 1

        prune_labels(ts)
        best_label = [None]*LABEL_COUNT
        best_gap = PAIR_WINDOW + 1
        for l_ts, l_data in label_buffer:
            gap = abs(ts - l_ts)
            if gap < best_gap:
                best_gap = gap
                best_label = l_data
        if best_gap > PAIR_WINDOW:
            unpaired_cnt += 1

        pressure_mat[:] = np.array(matrix).T
        csv_writer.writerow([ts] + pressure_mat.flatten().tolist() + best_label)
        rows_written += 1
        updated_heat = True

    if time.time() - last_report >= 1:
        print(f"FlexGrid {flex_cnt}  LASK5 {label_cnt}  CSV {rows_written}  Unpaired {unpaired_cnt}")
        last_report = time.time()

    artists = []
    if updated_heat:
        im.set_array(pressure_mat)
        artists.append(im)
    if updated_bar and latest_label:
        for bar, h in zip(bars, latest_label[1]):
            bar.set_height(h if h is not None else 0)
        artists.extend(bars)
    return artists

anim = FuncAnimation(fig, step, interval=40, cache_frame_data=False)

try:
    plt.show()
finally:
    csv_fp.close(); raw_fp.close()
    print("CSV →", CSV_PATH)
    print("RAW →", RAW_PATH)
